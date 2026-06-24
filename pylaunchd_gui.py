#!/usr/bin/env python3
# -*- coding: ascii -*-

import operator
import os
import re
import shlex
import subprocess
import sys
import html
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets

    QT_API = "PySide6"

except ImportError:
    from PyQt6 import QtCore, QtGui, QtWidgets

    # PySide-compatible names when using PyQt6
    QtCore.Signal = QtCore.pyqtSignal
    QtCore.Slot = QtCore.pyqtSlot
    QtCore.Property = QtCore.pyqtProperty

    QT_API = "PyQt6"
"""
> man launchd:
FILES
     ~/Library/LaunchAgents         Per-user agents provided by the user.
     /Library/LaunchAgents          Per-user agents provided by the administrator.
     /Library/LaunchDaemons         System-wide daemons provided by the administrator.
     /System/Library/LaunchAgents   Per-user agents provided by Apple.
     /System/Library/LaunchDaemons  System-wide daemons provided by Apple.

SEE ALSO
     launchctl(1), launchd.plist(5),

https://apple.stackexchange.com/questions/399086/how-to-use-launchctl-print-as-a-replacement-for-launchctl-bslist


- search by label/path 
- add DOMAINS dropdown:
    - system (launchctl print system)
    - user (launchctl print user/`id -u`)
    - user (launchctl print user/$UID)
    - gui (launchctl print gui/$UID)

- get service info
launchctl print gui/501/yanue.v2rayu.v2ray-core
                    ^^ = id -u

- Load (DEPRECATED)
launchctl load -w ~/Library/LaunchAgents/caddy.plist

- Unload (DEPRECATED)
launchctl unload -w ~/Library/LaunchAgents/caddy.plist

- NEW WAY (requires target domain + uid = `id -u`, except for 'system')
> load
launchctl bootstrap gui/UID some.plist

> unload
launchctl bootout gui/UID some.plist
launchctl bootout user/UID some.plist
launchctl bootout login/UID some.plist

> list all
launchctl list 

> NEW WAY
launchctl print <domain>/<UID>

> see https://gist.github.com/masklinn/a532dfe55bdeab3d60ab8e46ccc38a68
launchctl print system

> disable job for root
sudo launchctl disable user/0/test

> disabled jobs for user root (uid=0)
sudo launchctl print-disabled user/0


Additional references:
https://developer.apple.com/library/archive/technotes/tn2083/_index.html#//apple_ref/doc/uid/DTS10003794
https://apple.stackexchange.com/a/105897

Daemons and Services Programming Guide
======================================

> man pages:

* man launchctl
* man launchd
* man launchd.plist
"""

APPNAME = "pyLaunchd"
VERSION = f"14.0602.16 ({QT_API})"

LAUNCHD_DOMAINS = ["User", "System", "GUI"]

DEFAULT_EDITOR = "system"

DEBUG = False

# launchctl print is I/O-bound (it forks+execs a process and waits on pipes),
# so a worker pool larger than the CPU count gives a large wall-clock win when
# a domain has hundreds/thousands of jobs, without overwhelming the OS.
MAX_WORKERS = 16

# Precompiled once instead of per-job (load_data_launchctl can call these
# thousands of times for a busy domain).
RE_SERVICES_BLOCK = re.compile(r"services = \{\n(.*?)\n\t\}", re.DOTALL)
RE_PATH = re.compile(r"^\s+path =\s(.*)$", re.MULTILINE)
RE_STATE = re.compile(r"^\s+state =\s(.*)$", re.MULTILINE)


def run_launchctl(args, privileged=False):
    """Run a command and return (stdout, stderr) as text.

    Plain subprocess wrapper with no Qt/GUI interaction, so it is safe to
    call from worker threads (unlike MainWindow.exec, which may pop up a
    QMessageBox on error and therefore must stay on the GUI thread).

    privileged=True runs the command through a native macOS admin-password
    prompt (osascript "with administrator privileges") instead of directly -
    needed for the system domain, since LaunchDaemons require root. The
    command is shell-escaped once (shlex.quote per argument) and then
    AppleScript-escaped once more, since "do shell script" takes the whole
    command as a single AppleScript string literal that is itself handed to
    /bin/sh.
    """
    if DEBUG:
        print(f"CMD{' (privileged)' if privileged else ''}: {' '.join(args)}")
    if privileged:
        shell_cmd = " ".join(shlex.quote(a) for a in args)
        escaped = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
        osa_script = f'do shell script "{escaped}" with administrator privileges'
        proc = subprocess.run(
            ["osascript", "-e", osa_script], capture_output=True, text=True
        )
    else:
        proc = subprocess.run(args, capture_output=True, text=True)
    return proc.stdout, proc.stderr


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()

        self.read_settings()

        self.iconSwitch = self.style().standardIcon(
            QtWidgets.QStyle.StandardPixmap.SP_MediaSkipForward
        )
        self.setWindowIcon(self.iconSwitch)

        self.setGeometry(100, 150, 500, 660)

        self.jobs = {}
        self.createActions()
        self.createMenus()
        self.createToolBars()
        self.data = self.load_data_launchctl(self.domain_id)
        self.data_all = []
        self._set_full_dataset(self.data)
        self.statusBar().showMessage(f"Total jobs: {len(self.data)}")

        self.textEdit = QtWidgets.QTextEdit()
        self.textEdit.setReadOnly(True)

        self.setCentralWidget(self.textEdit)

        # self.createStatusBar()
        self.createDockWindows()
        self.setWindowTitle(APPNAME)

        self.setUnifiedTitleAndToolBarOnMac(True)
        self.tableView.configureTableView()

        if self.is_toolbar_hidden:
            self.actionToggleToolbar.setChecked(True)
            self.on_toggle_toolbar()

    def exec(self, args):
        if DEBUG:
            print(f"CMD: {' '.join(args)}")

        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0:
            show_gui_error(str(args), "{proc.returncode}: {proc.stderr}")
        return proc.stdout

    def _set_full_dataset(self, data):
        """Replace the unfiltered dataset and rebuild the lowercase search cache.

        Lowercasing every label/path once here (instead of on every keystroke
        in on_search_changed) is what keeps the search box responsive even
        with thousands of jobs loaded.
        """
        self.data_all[:] = data
        self._search_keys = [
            (label.lower(), path.lower()) for label, path, _state in data
        ]

    def initialize_data(self, idx=0):
        try:
            self.tableView.tableModel.sendSignalLayoutAboutToBeChanged()
            self.data[:] = self.load_data_launchctl(idx)
            self._set_full_dataset(self.data)
            self.tableView.tableModel.sendSignalLayoutChanged()
        except Exception as e:
            print("Error initializing data", e)

    def on_about(self):
        QtWidgets.QMessageBox.about(
            self,
            "Abouts",
            "%s<br/><br/>"
            "Version: %s<br/><br/>"
            "From: <a href='mailto:slavery.two.point.zero@gmail.com'>slavery.two.point.zero@gmail.com</a><br/><br/>"
            "Subject: For a moment, nothing happened.&nbsp;Then, after a second or so, nothing continued to happen...<br/><br/>"
            "Ponty Mython<br>Drain Bamage Season 2<br>&copy; 2022" % (APPNAME, VERSION),
        )

    def selected_job(self):
        """Return (label, plist_path) for the selected row, or None (and
        show the standard error) if nothing is selected."""
        selected_indexes = self.tableView.selectionModel().selectedRows()
        if not selected_indexes:
            show_gui_error("Please select a job first!")
            return None
        row = self.data[selected_indexes[0].row()]
        return row[0], row[1]

    def domain_target(self, domain_id=None):
        """launchctl domain-target string for a domain id, e.g. "system",
        "user/501", "gui/501". Shared by the data loader and the job
        actions so they always agree on which domain is "current" - this is
        what the old load/unload-based actions got wrong (they ignored the
        selected domain entirely and always landed in the app's own
        ambient gui/$UID session)."""
        if domain_id is None:
            domain_id = self.domain_id
        domain = LAUNCHD_DOMAINS[domain_id].lower()
        uid = os.getuid()
        user_identifier = "" if domain == "system" else f"/{uid}"
        return f"{domain}{user_identifier}"

    def run_domain_action(self, args, show_errors=True):
        """Run a launchctl mutation against the currently selected domain.

        Transparently elevates via a native admin-password prompt when the
        target is the system domain (LaunchDaemons require root and this
        app never runs as root itself). Returns (stdout, stderr); callers
        that need to try a fallback command (Start tries kickstart, then
        falls back to bootstrap) pass show_errors=False so an expected
        first failure doesn't pop a dialog before the fallback even runs.
        """
        privileged = self.domain_target().startswith("system")
        stdout, stderr = run_launchctl(args, privileged=privileged)
        if stderr and show_errors:
            show_gui_error(str(args), stderr)
        return stdout, stderr

    def refresh_job_state(self, label):
        """Re-query just this one job's state and update its row in place,
        instead of reloading the whole domain. self.data's rows are the
        same list objects as self.data_all's (filtering only changes which
        rows are *visible*, never copies them), so mutating the row found
        here is automatically reflected in data_all too."""
        details, _err = run_launchctl(
            ["launchctl", "print", f"{self.domain_target()}/{label}"]
        )
        self.jobs[label] = details
        state_match = RE_STATE.search(details)
        new_state = state_match.group(1) if state_match else ""
        for row_index, row in enumerate(self.data):
            if row[0] == label:
                row[2] = new_state
                model = self.tableView.tableModel
                cell = model.index(row_index, 2)
                model.dataChanged.emit(cell, cell)
                break

    def on_start_job(self, which):
        job = self.selected_job()
        if not job:
            return
        label, plist_path = job
        service_target = f"{self.domain_target()}/{label}"

        # Every row in this table came from "launchctl print <domain>", so
        # the job is by definition already bootstrapped; kickstart is the
        # correct verb to make an idle/on-demand job run right now.
        # bootstrap is only needed as a fallback for a job that was bootout
        # earlier in this session (e.g. via Stop) without the table having
        # been refreshed since, so it's no longer actually loaded.
        _out, err = self.run_domain_action(
            ["launchctl", "kickstart", service_target], show_errors=False
        )
        if err:
            _out, err = self.run_domain_action(
                ["launchctl", "bootstrap", self.domain_target(), plist_path],
                show_errors=False,
            )
        if err:
            show_gui_error(f"launchctl kickstart/bootstrap {service_target}", err)
        else:
            self.statusBar().showMessage(f"Started {label}")
        self.refresh_job_state(label)

    def on_stop_job(self, which):
        job = self.selected_job()
        if not job:
            return
        label, _plist_path = job
        service_target = f"{self.domain_target()}/{label}"

        # bootout targets the service by domain+label directly, so unlike
        # the old path-based "unload" it still works even if the plist was
        # since moved or deleted.
        _out, err = self.run_domain_action(
            ["launchctl", "bootout", service_target], show_errors=False
        )
        if err:
            show_gui_error(f"launchctl bootout {service_target}", err)
        else:
            self.statusBar().showMessage(f"Stopped {label}")
        self.refresh_job_state(label)

    def on_enable_job(self, which):
        job = self.selected_job()
        if not job:
            return
        label, plist_path = job
        service_target = f"{self.domain_target()}/{label}"

        # enable/disable (persisted) and bootstrap/bootout/kickstart
        # (runtime) are independent in modern launchd - there is no single
        # subcommand equivalent to the old "load -w". enable must run
        # before kickstart/bootstrap: a disabled service refuses to start
        # until its override is cleared first.
        _out, enable_err = self.run_domain_action(
            ["launchctl", "enable", service_target], show_errors=False
        )
        _out, start_err = self.run_domain_action(
            ["launchctl", "kickstart", service_target], show_errors=False
        )
        if start_err:
            _out, start_err = self.run_domain_action(
                ["launchctl", "bootstrap", self.domain_target(), plist_path],
                show_errors=False,
            )
        errors = "\n".join(e for e in (enable_err, start_err) if e)
        if errors:
            show_gui_error(f"launchctl enable/kickstart {service_target}", errors)
        else:
            self.statusBar().showMessage(f"Enabled {label}")
        self.refresh_job_state(label)

    def on_disable_job(self, which):
        job = self.selected_job()
        if not job:
            return
        label, _plist_path = job
        service_target = f"{self.domain_target()}/{label}"

        _out, bootout_err = self.run_domain_action(
            ["launchctl", "bootout", service_target], show_errors=False
        )
        _out, disable_err = self.run_domain_action(
            ["launchctl", "disable", service_target], show_errors=False
        )
        errors = "\n".join(e for e in (bootout_err, disable_err) if e)
        if errors:
            show_gui_error(f"launchctl bootout/disable {service_target}", errors)
        else:
            self.statusBar().showMessage(f"Disabled {label}")
        self.refresh_job_state(label)

    def on_show_in_finder(self, which):
        job = self.selected_job()
        if not job:
            return
        _label, path = job
        result = self.exec(["open", "-R", path])
        if result:
            self.statusBar().showMessage(result)

    def on_refresh(self, which):
        domain_index = self.comboBoxDomain.currentIndex()
        self.statusBar().showMessage(
            f"Refreshing domain {LAUNCHD_DOMAINS[domain_index]} - please wait..."
        )
        self.initialize_data(domain_index)
        self.statusBar().showMessage(f"Total jobs: {len(self.data)}")

        if self.searchBox.text():
            self.on_search_changed(self.searchBox.text())

    def createActions(self):
        self.actionOpenFile = QtGui.QAction(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ArrowRight),
            "&Open...",
            self,
            shortcut=QtGui.QKeySequence.StandardKey.Forward,
            statusTip="Open associated plist file",
            triggered=self.on_open_linked_file,
        )

        self.actionToggleToolbar = QtGui.QAction(
            self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_DialogCloseButton
            ),
            "Hide toolbar...",
            self,
            shortcut=QtGui.QKeySequence.StandardKey.Bold,
            statusTip="Show or hide toolbar",
            triggered=self.on_toggle_toolbar,
        )

        self.actionSetEditor = QtGui.QAction(
            self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView
            ),
            "Set editor...",
            self,
            statusTip="Set editor app for viewing plist files",
            triggered=self.on_editor_config,
        )

        self.actionQuit = QtGui.QAction(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserStop),
            "&Quit",
            self,
            statusTip="Quit the application",
            triggered=self.close,
        )

        self.actionAbout = QtGui.QAction(
            self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_FileDialogInfoView
            ),
            "About",
            self,
            statusTip="Show the About box",
            triggered=self.on_about,
        )

        self.actionStart = QtGui.QAction(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaPlay),
            "Start",
            self,
            statusTip="Start job",
            triggered=self.on_start_job,
        )

        self.actionStop = QtGui.QAction(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaStop),
            "Stop",
            self,
            statusTip="Stop job",
            triggered=self.on_stop_job,
        )

        self.actionEnable = QtGui.QAction(
            self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_DialogApplyButton
            ),
            "Start +w",
            self,
            statusTip="Enable job",
            triggered=self.on_enable_job,
        )

        self.actionDisable = QtGui.QAction(
            self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_DialogCancelButton
            ),
            "Stop -w",
            self,
            statusTip="Disable job",
            triggered=self.on_disable_job,
        )

        self.actionShowInFinder = QtGui.QAction(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DirIcon),
            "Show in Finder",
            self,
            statusTip="Show plist file in finder",
            triggered=self.on_show_in_finder,
        )

        self.actionRefresh = QtGui.QAction(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload),
            "Refresh",
            self,
            statusTip="Refresh",
            triggered=self.on_refresh,
        )

    def on_toggle_toolbar(self):
        self.is_toolbar_hidden = self.actionToggleToolbar.isChecked()
        self.toolBar.setVisible(not self.is_toolbar_hidden)

    def on_editor_config(self):
        value, ok = QtWidgets.QInputDialog.getText(
            self,
            "Configure Editor",
            "Editor name or command line",
            QtWidgets.QLineEdit.EchoMode.Normal,
            self.editor,
        )
        if ok:
            self.editor = value
            self.statusBar().showMessage(f'Editor="{self.editor}"')

    def on_domain_changed(self, selected_index):
        self.domain_id = selected_index
        self.statusBar().showMessage(
            f"Loading jobs for domain [{LAUNCHD_DOMAINS[selected_index]}] - please wait..."
        )
        self.initialize_data(selected_index)
        self.statusBar().showMessage(f"Total jobs: {len(self.data)}")

        if self.searchBox.text():
            self.on_search_changed(self.searchBox.text())

    def on_search_changed(self, text):
        query = text.strip().lower()
        if query:
            self.statusBar().showMessage(f"Filter by: {text}")

        try:
            self.tableView.tableModel.sendSignalLayoutAboutToBeChanged()
            if query:
                # _search_keys holds precomputed (label.lower(), path.lower())
                # tuples in lockstep with data_all, so filtering never has to
                # re-lowercase the same strings on every keystroke.
                filtered_data = [
                    row
                    for row, (label_lc, path_lc) in zip(
                        self.data_all, self._search_keys
                    )
                    if query in label_lc or query in path_lc
                ]
            else:
                filtered_data = list(self.data_all)
            self.data[:] = filtered_data
            self.tableView.tableModel.sendSignalLayoutChanged()
        except Exception as e:
            self.statusBar().showMessage(str(e))
            print("Error filtering data", e)

    def createMenus(self):
        self.setMenuBar(QtWidgets.QMenuBar())
        self.fileMenu = self.menuBar().addMenu("&File")
        self.fileMenu.addAction(self.actionQuit)
        self.viewMenu = self.menuBar().addMenu("&View")
        self.viewMenu.addAction(self.actionToggleToolbar)
        self.configMenu = self.menuBar().addMenu("&Config")
        self.configMenu.addAction(self.actionSetEditor)
        self.helpMenu = self.menuBar().addMenu("&Help")
        self.helpMenu.addAction(self.actionAbout)
        self.helpMenu.addAction(self.actionRefresh)

    def createToolBars(self):
        self.toolBar = self.addToolBar("&File")
        self.toolBar.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextUnderIcon
        )
        self.comboBoxDomain = QtWidgets.QComboBox()
        self.comboBoxDomain.insertItems(0, LAUNCHD_DOMAINS)
        self.comboBoxDomain.setCurrentIndex(self.domain_id)
        self.comboBoxDomain.activated.connect(self.on_domain_changed)
        self.toolBar.addWidget(self.comboBoxDomain)
        self.toolBar.addAction(self.actionOpenFile)
        self.toolBar.addAction(self.actionStart)
        self.toolBar.addAction(self.actionStop)
        self.toolBar.addAction(self.actionEnable)
        self.toolBar.addAction(self.actionDisable)
        self.toolBar.addAction(self.actionRefresh)
        # self.toolBar.addAction(self.actionSetEditor)
        self.toolBar.addAction(self.actionAbout)
        self.toolBar.addAction(self.actionQuit)
        self.searchBox = QtWidgets.QLineEdit(self, placeholderText="search...")
        self.searchBox.textChanged.connect(self.on_search_changed)
        self.toolBar.addWidget(self.searchBox)

    def load_data_launchctl(self, domain_id=0):
        target = self.domain_target(domain_id)

        listing = self.exec(["launchctl", "print", target])
        match = RE_SERVICES_BLOCK.search(listing)
        if not match:
            self.jobs.clear()
            return []

        # dict.fromkeys preserves order while de-duplicating labels (cheap
        # insurance against launchctl listing the same label twice).
        labels = list(
            dict.fromkeys(
                line.split("\t")[-1].strip() for line in match.group(1).splitlines()
            )
        )
        labels = [label for label in labels if label]

        self.jobs.clear()
        data = []

        # The bulk listing above gives us labels but not each job's plist
        # path/state, so we still need one "launchctl print <label>" call per
        # job - but those calls are independent and I/O-bound, so running
        # them concurrently turns an O(n) sequence of process spawns into a
        # few batches of MAX_WORKERS, which is the difference between
        # "instant" and "minutes" once a domain has thousands of jobs.
        # run_launchctl (unlike self.exec) never touches the GUI, so it's
        # safe to call from these worker threads; executor.map keeps results
        # in label order even though completion order may vary.
        commands = [["launchctl", "print", f"{target}/{label}"] for label in labels]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for label, (details, _err) in zip(
                labels, pool.map(run_launchctl, commands)
            ):
                self.jobs[label] = details
                path_match = RE_PATH.search(details)
                path = path_match.group(1) if path_match else None
                if path and path.startswith("/"):
                    state_match = RE_STATE.search(details)
                    data.append(
                        [label, path, state_match.group(1) if state_match else ""]
                    )

        return data

    def createDockWindows(self):
        self.topDock = QtWidgets.QDockWidget(self)
        self.topDock.setFeatures(
            QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.topDock.setAllowedAreas(
            QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
            | QtCore.Qt.DockWidgetArea.RightDockWidgetArea
            | QtCore.Qt.DockWidgetArea.TopDockWidgetArea
            | QtCore.Qt.DockWidgetArea.BottomDockWidgetArea
        )

        self.tableView = CustomTableView(self.data)
        self.tableView.addAction(self.actionOpenFile)
        self.tableView.addAction(self.actionStart)
        self.tableView.addAction(self.actionStop)
        self.tableView.addAction(self.actionEnable)
        self.tableView.addAction(self.actionDisable)
        self.tableView.addAction(self.actionShowInFinder)
        self.tableView.setAppWindowHandle(self)

        self.tableView.setSelectionMode(
            QtWidgets.QTableView.SelectionMode.SingleSelection
        )
        self.topDock.setWindowTitle("registered services")

        self.topDock.setWidget(self.tableView)
        self.tableView.selectionModel().selectionChanged.connect(self.onListItemSelect)
        self.tableView.doubleClicked.connect(self.onListItemDoubleClick)

        self.addDockWidget(QtCore.Qt.DockWidgetArea.TopDockWidgetArea, self.topDock)

        self.bottomDock = QtWidgets.QDockWidget("", self)
        self.bottomDock.setFeatures(
            QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetVerticalTitleBar
        )
        self.bottomDock.setTitleBarWidget(QtWidgets.QWidget(self.bottomDock))
        # self.bottomDock.setFeatures(QtWidgets.QDockWidget.DockWidgetClosable)
        self.bottomDock.setAllowedAreas(
            QtCore.Qt.DockWidgetArea.BottomDockWidgetArea
            | QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        )

        ## hide initially
        self.bottomDock.hide()
        self.addDockWidget(
            QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, self.bottomDock
        )

    def clearLayout(self, layout):
        while layout.count():
            child = layout.takeAt(0)
            if child.widget() is not None:
                child.widget().deleteLater()
            elif child.layout() is not None:
                self.clearLayout(child.layout())

    def onListItemDoubleClick(self, qModelIndex):
        self.on_open_linked_file(row_index=qModelIndex.row())

    def onListItemSelect(self, selected):
        """An item in the table has been clicked/selected.

        :param selected: QItemSelection of newly-selected cells. Can be empty
            (e.g. a model reset from search/refresh clears the selection
            without selecting anything new), so that case must be handled
            instead of unconditionally indexing into it.
        """
        if selected.isEmpty():
            return

        rowIndex = selected.first().top()
        row_data = self.data[rowIndex]
        job_details = self.jobs.get(row_data[0])
        self.textEdit.setHtml(
            f"""
<pre>
{html.escape(job_details)}
</pre>
"""
        )

        self.statusBar().showMessage(row_data[1])

    def on_open_linked_file(self, row_index=None):
        if row_index is None:
            selected_indexes = self.tableView.selectionModel().selectedRows()

            if len(selected_indexes):
                row_index = selected_indexes[0].row()
            else:
                show_gui_error("No job selected", "Please select a job first!")
                return
        plist_path = self.data[row_index][1]

        if plist_path and Path(plist_path).exists():
            self.start_file(plist_path)
        else:
            show_gui_error(
                "",
                f"There is no associated plist file for job {self.data[row_index][0]} "
                f"\nor invalid path [{plist_path}]",
            )

    def start_file(self, filepath):
        if not self.editor:
            self.editor = "system"

        if self.editor == "system":
            self.exec(("open", filepath))
        else:
            if self.editor.startswith("/"):
                self.exec((self.editor, filepath))
            elif "-" in self.editor:
                self.exec(self.editor.split() + [filepath])
            else:
                self.exec(("open", "-a", self.editor, filepath))

    def read_settings(self):
        self.settings = QtCore.QSettings(
            QtCore.QSettings.Format.IniFormat,
            QtCore.QSettings.Scope.UserScope,
            "xh",
            APPNAME,
        )
        pos = self.settings.value("pos", QtCore.QPoint(200, 200))
        size = self.settings.value("size", QtCore.QSize(600, 400))
        self.resize(size)
        self.move(pos)

        self.domain_id = self.settings.value("domain_id", 0, type=int)
        if not 0 <= self.domain_id < len(LAUNCHD_DOMAINS):
            self.domain_id = 0

        self.is_toolbar_hidden = self.settings.value(
            "is_toolbar_hidden", False, type=bool
        )
        self.editor = self.settings.value("editor", DEFAULT_EDITOR, type=str)

    def write_settings(self):
        settings = QtCore.QSettings(
            QtCore.QSettings.Format.IniFormat,
            QtCore.QSettings.Scope.UserScope,
            "xh",
            APPNAME,
        )
        settings.setValue("pos", self.pos())
        settings.setValue("size", self.size())
        settings.setValue("is_toolbar_hidden", self.is_toolbar_hidden)
        settings.setValue("editor", self.editor)
        settings.setValue("domain_id", self.domain_id)
        settings.sync()

    def closeEvent(self, event):
        self.write_settings()


class CustomTableView(QtWidgets.QTableView):
    def __init__(self, table_data, *args):
        QtWidgets.QTableView.__init__(self, *args)
        self.tableModel = CustomTableModel(table_data, self)
        self.setModel(self.tableModel)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.ActionsContextMenu)

    def setAppWindowHandle(self, mainWindowHandle):
        self.mainWindow = mainWindowHandle

    def configureTableView(self):
        self.setShowGrid(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.setTabKeyNavigation(False)

        # disable row editing
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)

        # disable bold column headers
        horizontalHeader = self.horizontalHeader()
        horizontalHeader.setHighlightSections(False)

        self.style().pixelMetric(QtWidgets.QStyle.PixelMetric.PM_ScrollBarExtent)
        self.setWordWrap(True)
        self.setSortingEnabled(True)
        self.setSizeAdjustPolicy(
            QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents
        )
        self.resizeColumnsToContents()


# http://www.saltycrane.com/blog/2007/06/pyqt-42-qabstracttablemodelqtableview/
class CustomTableModel(QtCore.QAbstractTableModel):
    header_labels = ["Label", "Path", "State"]

    def __init__(self, datain, parent=None, *args):
        QtCore.QAbstractTableModel.__init__(self, parent, *args)
        self.arraydata = datain

    def rowCount(self, parent):
        return len(self.arraydata)

    def columnCount(self, parent):
        return len(self.header_labels)

    def data(self, qModelIndex, role):
        if not qModelIndex.isValid() or role != QtCore.Qt.ItemDataRole.DisplayRole:
            return None
        try:
            return self.arraydata[qModelIndex.row()][qModelIndex.column()]
        except IndexError:
            return None

    def setData(self, index, value, role=QtCore.Qt.ItemDataRole.EditRole):
        if role == QtCore.Qt.ItemDataRole.EditRole:
            self.arraydata[index.row()] = value
            self.dataChanged.emit(index, index)
            return True
        return False

    def sendSignalLayoutAboutToBeChanged(self):
        self.beginResetModel()

    def sendSignalLayoutChanged(self):
        self.endResetModel()

    def headerData(self, section, orientation, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if (
            role == QtCore.Qt.ItemDataRole.DisplayRole
            and orientation == QtCore.Qt.Orientation.Horizontal
        ):
            return self.header_labels[section]
        return QtCore.QAbstractTableModel.headerData(self, section, orientation, role)

    def insertRows(self, position, item, parent=QtCore.QModelIndex()):
        self.beginInsertRows(
            QtCore.QModelIndex(), len(self.arraydata), len(self.arraydata) + 1
        )
        self.arraydata.append(item)  # Item must be an array
        self.endInsertRows()
        return True

    def sort(self, ncol, order):
        """
        Sort table by given column number.
        """
        self.sendSignalLayoutAboutToBeChanged()
        self.arraydata.sort(
            key=operator.itemgetter(ncol),
            reverse=(order == QtCore.Qt.SortOrder.DescendingOrder),
        )
        self.sendSignalLayoutChanged()

    def flags(self, index):
        return QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable


def show_gui_error(msg, error_text=""):
    text = f"{msg}\n\n{error_text}" if (msg and error_text) else (msg or error_text)
    QtWidgets.QMessageBox.warning(None, APPNAME, text)


def main():
    app = QtWidgets.QApplication(sys.argv)
    mainWin = MainWindow()
    mainWin.show()
    mainWin.raise_()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
