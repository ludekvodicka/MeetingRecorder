import sys

from audiorecorder.shared.desktop.autoupdate.helper import UpdateHelper

if __name__ == "__main__":
    # Before audiorecorder.app, which imports PyQt6: in helper mode a new build only swaps
    # the files and must not start a second application.
    if UpdateHelper.requested(sys.argv):
        raise SystemExit(UpdateHelper.main(sys.argv[1:]))
    from audiorecorder.app import main

    main()
