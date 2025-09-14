import os
import re
import sys
from pathlib import Path

from google.protobuf import descriptor_pb2, descriptor_pool
from google.protobuf.descriptor import MethodDescriptor, ServiceDescriptor

from ._utils import run_protoc, run_protol


class ServiceMethod:
    """Represents a single RPC method in a service."""

    def __init__(self, method_desc: MethodDescriptor):
        self.name = self._to_snake_case(method_desc.name)
        self.original_name = method_desc.name
        self.input_type = method_desc.input_type.name
        self.output_type = method_desc.output_type.name
        self.full_input_type = method_desc.input_type.full_name
        self.full_output_type = method_desc.output_type.full_name

    def _to_snake_case(self, name: str) -> str:
        """Convert CamelCase to snake_case."""

        s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


class Service:
    def __init__(self, service_desc: ServiceDescriptor):
        self.name = service_desc.name
        self.full_name = service_desc.full_name
        self.methods = [ServiceMethod(method) for method in service_desc.methods]


class ProtobufGenerator:
    def __init__(self, root_dir: Path, src_dir: Path):
        self.root_dir = root_dir
        self.src_dir = src_dir
        self.proto_dir = (root_dir / "proto").absolute()
        self.proto_files = [str(p.absolute()) for p in self.proto_dir.glob("*.proto")]
        self.services: list[Service] = []
        self.message_types: set[str] = set()

    def _parse_proto_files(self) -> None:
        descriptor_set_file = self.src_dir / "proto" / "descriptors.pb"
        run_protoc(
            f"--descriptor_set_out={descriptor_set_file}",
            "--include_imports",
            f"--proto_path={self.proto_dir}",
            *self.proto_files,
        )
        descriptor_set = descriptor_pb2.FileDescriptorSet()
        with open(descriptor_set_file, "rb") as f:
            descriptor_set.ParseFromString(f.read())

        pool = descriptor_pool.DescriptorPool()
        for file_desc_proto in descriptor_set.file:
            pool.Add(file_desc_proto)

        for file_desc_proto in descriptor_set.file:
            if file_desc_proto.service:
                file_desc = pool.FindFileByName(file_desc_proto.name)
                for service_desc in file_desc.services_by_name.values():
                    service = Service(service_desc)
                    self.services.append(service)
                    for method in service.methods:
                        self.message_types.add(method.input_type)
                        self.message_types.add(method.output_type)

    def _generate_service_class(self, service: Service) -> list[str]:
        lines = [
            f"class {service.name}Base(ABC):",
            f'    """Abstract base class for {service.name}.',
            "    ",
            f"    This class defines the interface for the {service.name}"
            " as specified in the protobuf files.",
            "    All methods must be implemented by concrete subclasses.",
            '    """',
        ]

        if not service.methods:
            lines.extend(
                [
                    "    ",
                    "    # No methods defined in this service",
                    "    pass",
                ]
            )
        else:
            lines.append("")
            for i, method in enumerate(service.methods):
                lines.extend(self._generate_method(method))
                if i < len(service.methods) - 1:
                    lines.append("")

        return lines

    def _generate_method(self, method: ServiceMethod) -> list[str]:
        return [
            "    @classmethod",
            f"    def {method.name}_raw(cls, data: bytes) -> bytes:",
            f'        """{method.original_name} RPC method.',
            "        ",
            "        Args:",
            "            data: bytes containing the raw request data",
            "            ",
            "        Returns:",
            "            bytes containing the response data",
            '        """',
            f"        request = {method.input_type}.FromString(data)",
            f"        response = cls.{method.name}(request)",
            "        return response.SerializeToString()",
            "\n",
            "    @classmethod",
            "    @abstractmethod",
            f"    def {method.name}(cls, request: {method.input_type})"
            f" -> {method.output_type}:",
            f'        """{method.original_name} RPC method.',
            "        ",
            "        Args:",
            f"            request: {method.input_type} containing the request data",
            "            ",
            "        Returns:",
            f"            {method.output_type} containing the response data",
            "            ",
            "        Raises:",
            "            NotImplementedError: "
            "This method must be implemented by subclasses",
            '        """',
            f'        raise NotImplementedError("{method.name} '
            'method must be implemented")',
        ]

    def generate_python_definitions(self) -> None:
        proto_py_out_dir = (self.src_dir / "proto").absolute()
        proto_py_out_dir.mkdir(exist_ok=True)
        run_protoc(
            f"--proto_path={self.proto_dir}",
            f"--python_out={proto_py_out_dir}",
            f"--pyi_out={proto_py_out_dir}",
            *self.proto_files,
        )
        run_protol(
            "--create-package",
            "--in-place",
            "--python-out",
            str(proto_py_out_dir),
            "protoc",
            f"--proto-path={self.proto_dir}",
            *self.proto_files,
        )

    def generate_ts_definitions(self) -> None:
        proto_ts_out_dir = (
            self.root_dir / "ts" / "src" / "lib" / "generated"
        ).absolute()
        proto_ts_out_dir.mkdir(exist_ok=True, parents=True)
        node_bin_dir = self.root_dir / "ts" / "node_modules" / ".bin"
        protoc_es_plugin_path = node_bin_dir / "protoc-gen-es"
        if sys.platform == "win32":
            protoc_es_plugin_path = protoc_es_plugin_path.with_suffix(".cmd")
        path_sep = ";" if sys.platform == "win32" else ":"
        run_protoc(
            f"--proto_path={self.proto_dir}",
            "--plugin",
            f"{protoc_es_plugin_path}",
            f"--es_out={proto_ts_out_dir}",
            *self.proto_files,
            env={**os.environ, "PATH": f"{node_bin_dir}{path_sep}{os.environ['PATH']}"},
        )

    def generate_python_services(self) -> None:
        lines = [
            '"""',
            "Abstract service classes generated from protobuf services.",
            "",
            "This module contains abstract base classes for all services "
            "defined in the protobuf files.",
            "Implement these classes to provide concrete service implementations.",
            "",
            "This file is auto-generated by ankiscripts. Do not edit manually.",
            '"""',
            "",
            "from abc import ABC, abstractmethod",
            "",
        ]

        if self.message_types:
            pb2_modules = set()
            for proto_file in self.proto_dir.glob("*.proto"):
                pb2_module = proto_file.stem + "_pb2"
                pb2_modules.add(pb2_module)

            for pb2_module in sorted(pb2_modules):
                lines.append(f"from .{pb2_module} import (")

                module_types = sorted(self.message_types)
                for i, msg_type in enumerate(module_types):
                    comma = "," if i < len(module_types) - 1 else ""
                    lines.append(f"    {msg_type}{comma}")

                lines.append(")")

            lines.append("")

        for service in self.services:
            lines.extend(self._generate_service_class(service))
            lines.append("")

        code = "\n".join(lines)
        services_file = self.src_dir / "proto" / "services.py"
        services_file.write_text(code, encoding="utf-8")

    def generate_flask_routes(self) -> None:
        lines = [
            "from ..backend.services import *",
            "from ..vendor.ankiutils.sveltekit import SveltekitServer\n\n"
            "def add_api_routes(server: SveltekitServer) -> None:",
        ]
        for service in self.services:
            for method in service.methods:
                lines.append(
                    f"    server.add_proto_handler('{service.full_name}',"
                    f" '{method.original_name}', {service.name}.{method.name}_raw)"
                )
        code = "\n".join(lines) + "\n"
        routes_file = self.src_dir / "proto" / "routes.py"
        routes_file.write_text(code, encoding="utf-8")

    def generate(
        self,
    ) -> None:
        self.generate_python_definitions()
        self._parse_proto_files()
        self.generate_python_services()
        self.generate_flask_routes()
        self.generate_ts_definitions()
