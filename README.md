# Anki Add-on Build Script

A build script for Anki add-ons intended for my own use.
Take a look at [glutanimate/anki-addon-builder](https://github.com/glutanimate/anki-addon-builder)
if you want something more mature and tested.

The [ankibuild](./ankibuild.py) file is a build script inspired by [ankitects/anki-addons](https://github.com/ankitects/anki-addons) and [glutanimate/anki-addon-builder](https://github.com/glutanimate/anki-addon-builder).

## Structure

- Source files are assumed to reside in the [src](src) directory.
- Qt Designer files (if any) are assumed to be in [designer](designer).
- An [addon.json](addon.json) file is used for metadata, similar to Glutanimate's add-on builder. The build script copies all properties defined there to a file called consts.py that's bundled with the add-on.
- Builds are written to the build directory.

## Related

Also see my [Anki add-on template](https://github.com/abdnh/anki-addon-template).

## TODO

- [ ] tests
- [ ] docs
