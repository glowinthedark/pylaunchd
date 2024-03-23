# pylaunchd
MacOS launchd/launchctl GUI

- view macos launchagents and launchdaemons in the `user/system/gui` domains and display detailed properties for each service
- start/stop/enable/disable jobs (WIP) 
- edit service launchers with user configurable editor; some `.plist` files are plain text XML's, for binary `.plist` files consider e.g. [TextMate](https://macromates.com/) or [SublimeText](https://www.sublimetext.com/) with [binary plist package](https://packagecontrol.io/packages/BinaryPlist) installed) 


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
