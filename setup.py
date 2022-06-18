from distutils.core import setup

setup(
    name="ankibuild",
    version="0.1.0",
    description="Simple script to build Anki add-ons",
    author="Abdo",
    author_email="abd.nh25@gmail.com",
    url="https://github.com/abdnh/ankibuild",
    py_modules=["ankibuild", "ankirun"],
    extras_require={
        "qt5": ["pyqt5"],
        "qt6": ["pyqt6"],
    },
)
