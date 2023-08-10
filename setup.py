from setuptools import find_packages, setup

setup(
    name="ankiscripts",
    version="1.3.0",
    description="A collection of scripts to build my Anki add-ons",
    author="Abdo",
    author_email="abdo@abdnh.net",
    url="https://github.com/abdnh/ankiscripts",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: OS Independent",
    ],
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    py_modules=["ankibuild"],
    package_data={"ankiscripts": ["*.md", "*.html"]},
    install_requires=["jsonschema"],
    extras_require={
        "qt5": ["pyqt5"],
        "qt6": ["pyqt6"],
        "forms": ["pyqt6"],
    },
)
