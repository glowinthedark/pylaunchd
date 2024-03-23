# pylaunchd
MacOS launchd/launchctl GUI

- view macos launchagents and launchdaemons user/system/gui and properties
- edit with user configurable editor (recommended for binary `.plist` files: TextMate or SublimeText with binary plist package installed) 
- start/stop/enable/disable jobs (WIP) 

![](pylaunchd-screenshot.png)

## Installation and dependencies

To run the app the following dependencies are needed (assuming [homebrew](https://brew.sh/) is already installed):

- python3 — normally, already present on modern macos versions, or can be installed with `brew install python`
- qt5 - install with `brew install qt5`
- pyqt5 - install with `pip3 install pyqt5`

## Usage 

The program is contained in a single file and can be launched with: 

```bash
python3 pylaunchd_gui.py
```


### Other launchd GUI apps

- [Lingon X](https://www.peterborgapps.com/lingon/)
- [LaunchControl](https://www.soma-zone.com/LaunchControl/)
