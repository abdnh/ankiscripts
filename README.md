# Anki Add-on Scripts

Scripts for Anki add-on development intended for my own use. The file structure used in my [add-on template](https://github.com/abdnh/anki-addon-template) is expected by scripts here.

## Scripts

### ankibuild.py

A script to package add-on sources into an .ankiaddon file. It's inspired by [ankitects/anki-addons](https://github.com/ankitects/anki-addons) and [glutanimate/anki-addon-builder](https://github.com/glutanimate/anki-addon-builder). Take a look at Glutanimate's add-on builder if you want something more mature and tested.

### ankirun.py

This script runs Anki with the base folder `ankidata` in the current directory and some useful env variables set for debugging.
This is intended for testing the add-on after building and copying src/ to ankidata/addons21 or symlinking it.

### ankinit.py

This script automates some chores I used to do manually when setting up a new add-on using my add-on template (e.g. modifying README.md and addon.json, and symlinking the src folder to an Anki base folder.)
