from setuptools import find_packages, setup

setup(
    name="ankibuild",
    version="1.0.0",
    description="Simple script to build Anki add-ons",
    author="Abdo",
    author_email="abd.nh25@gmail.com",
    url="https://github.com/abdnh/ankibuild",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: OS Independent",
    ],
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    py_modules=["ankibuild"],
    install_requires=["jsonschema"],
    extras_require={
        "qt5": ["pyqt5"],
        "qt6": ["pyqt6"],
    },
)
