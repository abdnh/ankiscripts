# Anki Add-on Scripts

Scripts for Anki add-on development intended for my own use. The file structure used in my [add-on template](https://github.com/abdnh/anki-addon-template) is expected by scripts here.

## Scripts

### [build.py](src/ankiscripts/build.py)

A script to package add-on sources into an .ankiaddon file. It's inspired by [ankitects/anki-addons](https://github.com/ankitects/anki-addons) and [glutanimate/anki-addon-builder](https://github.com/glutanimate/anki-addon-builder). Take a look at Glutanimate's add-on builder if you want something more mature and tested.

### [run.py](src/ankiscripts/run.py)

This script runs Anki with the base folder `ankidata` in the current directory and some useful env variables set for debugging.
This is intended for testing the add-on after building and copying src/ to ankidata/addons21 or symlinking it.

### [init.py](src/ankiscripts/init.py)

This script automates some chores I used to do manually when setting up a new add-on using my add-on template (e.g. modifying README.md and addon.json, and symlinking the src folder to an Anki base folder.)

### [support.py](src/ankiscripts/support.py)

This is used to format the "Support & feature requests" and "Support me" sections of my add-on template's README
and set up add-on specific support links.

### [update.py](src/ankiscripts/update.py)

This script takes a path to an add-on and compares it with the template, offering to write non-existing files and output diffs of changed files. I use this to update add-ons with the latest changes to the template.

### [vendor.py](src/ankiscripts/vendor.py)

This installs libraries in requirements/bundle.txt (if found) to src/vendor. It has experimental support for handling dependencies with C modules by downloading platform-specific wheels and copying library files to the vendor folder.

### [update_deps.py](src/ankiscripts/update_deps.py)

This updates dependencies using [pip-tools](https://github.com/jazzband/pip-tools).

### [sourcedist.py](src/ankiscripts/sourcedist.py)

This creates a sources zip using git-archive and git-bundle.
