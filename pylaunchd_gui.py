#!/usr/bin/env python3
# Copyright 2026 glowinthedark
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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

# Precompiled once instead of per-job (collect_jobs can call these
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


def collect_jobs(target):
    """Bulk-load every job in a launchd domain.

    Returns ``(data, jobs)`` where ``data`` is a list of
    ``[label, path, state]`` rows (the table's canonical dataset) and
    ``jobs`` maps ``label -> raw "launchctl print <domain>/<label>"`` text
    (used to populate the details pane).

    This is a pure function with no Qt/GUI interaction, so it is safe to run
    on a background QThread. The per-job "launchctl print" calls are the
    expensive part (one fork+exec per service), but they are independent and
    I/O-bound, so running them concurrently turns an O(n) sequence of
    process spawns into a few batches of MAX_WORKERS. executor.map keeps
    results in label order even though completion order may vary.

    dict.fromkeys preserves order while de-duplicating labels (cheap
    insurance against launchctl listing the same label twice). Only jobs
    with an absolute "path =" are kept, matching the previous behaviour.
    """
    listing, _err = run_launchctl(["launchctl", "print", target])
    match = RE_SERVICES_BLOCK.search(listing)
    if not match:
        return [], {}

    labels = list(
        dict.fromkeys(
            line.split("\t")[-1].strip() for line in match.group(1).splitlines()
        )
    )
    labels = [label for label in labels if label]

    jobs = {}
    data = []
    commands = [["launchctl", "print", f"{target}/{label}"] for label in labels]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for label, (details, _err) in zip(labels, pool.map(run_launchctl, commands)):
            jobs[label] = details
            path_match = RE_PATH.search(details)
            path = path_match.group(1) if path_match else None
            if path and path.startswith("/"):
                state_match = RE_STATE.search(details)
                data.append([label, path, state_match.group(1) if state_match else ""])

    return data, jobs


class JobLoaderThread(QtCore.QThread):
    """Loads a domain's jobs off the GUI thread.

    Emits ``finished_jobs(data, jobs)`` on completion or ``error(msg)``.
    Keeping the heavy launchctl work off the GUI thread is what keeps the
    UI responsive (and the busy progress bar spinning) while a large domain
    is being parsed. Results are handed back via Qt signals, so the GUI
    thread never shares mutable state with the worker (no locking needed).
    """

    finished_jobs = QtCore.Signal(list, dict)
    error = QtCore.Signal(str)

    def __init__(self, target, parent=None):
        super().__init__(parent)
        self._target = target

    def run(self):
        try:
            data, jobs = collect_jobs(self._target)
            self.finished_jobs.emit(data, jobs)
        except Exception as e:
            self.error.emit(str(e))


class JobFilterProxyModel(QtCore.QSortFilterProxyModel):
    """Case-insensitive filter that matches the query against *either* the
    Label column or the Path column.

    Replacing the old hand-rolled list rebuild with a proxy model means
    filtering and sorting never mutate the underlying dataset (fixing the
    old sort-vs-search invariant bug), the view maps selection through
    mapToSource/mapFromSource automatically, and the search box collapses
    to a single setFilterFixedString call.
    """

    def filterAcceptsRow(self, source_row, source_parent):
        regex = self.filterRegularExpression()
        if not regex.pattern():
            return True
        model = self.sourceModel()
        label = (
            model.data(
                model.index(source_row, 0, source_parent),
                QtCore.Qt.ItemDataRole.DisplayRole,
            )
            or ""
        )
        path = (
            model.data(
                model.index(source_row, 1, source_parent),
                QtCore.Qt.ItemDataRole.DisplayRole,
            )
            or ""
        )
        return regex.match(label).hasMatch() or regex.match(path).hasMatch()


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
        self._loader = None
        self._progress = None
        self.createActions()
        self.createMenus()
        self.createToolBars()

        self.textEdit = QtWidgets.QTextEdit()
        self.textEdit.setReadOnly(True)

        self.setCentralWidget(self.textEdit)

        # createDockWindows builds the table view together with its source
        # model and the filter/sort proxy model that sits between the view
        # and the source, so they exist before the first (async) load.
        self.createDockWindows()
        self.setWindowTitle(APPNAME)

        self.setUnifiedTitleAndToolBarOnMac(True)
        self.tableView.configureTableView()

        if self.is_toolbar_hidden:
            self.actionToggleToolbar.setChecked(True)
            self.on_toggle_toolbar()

        self.statusBar().showMessage(
            f"Loading domain [{LAUNCHD_DOMAINS[self.domain_id]}] - please wait..."
        )
        self._start_load(self.domain_id)

    def exec(self, args):
        if DEBUG:
            print(f"CMD: {' '.join(args)}")

        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0:
            show_gui_error(str(args), f"{proc.returncode}: {proc.stderr}")
        return proc.stdout

    def on_about(self):
        QtWidgets.QMessageBox.about(
            self,
            "Abouts",
            "%s<br/><br/>"
            "Version: %s<br/><br/>"
            "From: <a href='mailto:slavery.two.point.zero@gmail.com'>slavery.two.point.zero@gmail.com</a><br/><br/>"
            "Subject: For a moment, nothing happened.&nbsp;Then, after a second or so, nothing continued to happen...<br/><br/>"
            "Licensed under the Apache License, Version 2.0<br><br>glowinthedark &copy; 2026"
            % (APPNAME, VERSION),
        )

    def selected_job(self):
        """Return (label, plist_path) for the selected row, or None (and
        show the standard error) if nothing is selected.

        The view's model is the proxy, so the selection's indexes are proxy
        indexes and must be mapped back to the source model before indexing
        into the canonical dataset."""
        selected_indexes = self.tableView.selectionModel().selectedRows()
        if not selected_indexes:
            show_gui_error("Please select a job first!")
            return None
        source_index = self.tableView.proxyModel.mapToSource(selected_indexes[0])
        row = self.tableView.tableModel.arraydata[source_index.row()]
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
        instead of reloading the whole domain. The source model's
        arraydata is the single canonical dataset (the proxy only changes
        which rows are *visible*, never copies them), so updating the row
        found here is reflected in the filtered view too. dataChanged is
        emitted on the source model and propagates through the proxy."""
        details, _err = run_launchctl(
            ["launchctl", "print", f"{self.domain_target()}/{label}"]
        )
        self.jobs[label] = details
        state_match = RE_STATE.search(details)
        new_state = state_match.group(1) if state_match else ""
        model = self.tableView.tableModel
        for row_index, row in enumerate(model.arraydata):
            if row[0] == label:
                row[2] = new_state
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
        self._start_load(domain_index)

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
        self.statusBar().showMessage(
            f"Loading jobs for domain [{LAUNCHD_DOMAINS[selected_index]}] - please wait..."
        )
        self._start_load(selected_index)

    def on_search_changed(self, text):
        # The proxy model owns all filtering; just hand it the query.
        # Case-insensitivity is configured once in CustomTableView.
        self.tableView.proxyModel.setFilterFixedString(text)
        if text.strip():
            self.statusBar().showMessage(f"Filter by: {text}")

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

    def _start_load(self, domain_id):
        """Kick off an asynchronous domain load on a background QThread.

        Disables the domain selector + refresh button and shows an
        indeterminate progress bar in the status bar while the worker
        runs, then re-enables them from the finished slot. A previous
        in-flight loader (if any) is allowed to finish and is replaced; in
        practice the UI controls that can trigger a second load are
        disabled while one is running."""
        self.domain_id = domain_id
        target = self.domain_target(domain_id)

        self._set_loading(True)

        self._loader = JobLoaderThread(target, self)
        self._loader.finished_jobs.connect(self._on_jobs_loaded)
        self._loader.error.connect(self._on_load_error)
        self._loader.finished.connect(self._loader.deleteLater)
        self._loader.start()

    def _set_loading(self, loading):
        if self._progress is None:
            self._progress = QtWidgets.QProgressBar()
            self._progress.setRange(0, 0)
            self._progress.setMaximumWidth(180)
            self._progress.setTextVisible(False)
            self.statusBar().addPermanentWidget(self._progress)
        self._progress.setVisible(loading)
        self.comboBoxDomain.setEnabled(not loading)
        self.actionRefresh.setEnabled(not loading)
        self.searchBox.setEnabled(not loading)

    def _on_jobs_loaded(self, data, jobs):
        self.jobs = jobs
        self.tableView.tableModel.set_jobs(data)
        self.tableView.resizeColumnsToContents()
        self.statusBar().showMessage(f"Total jobs: {len(data)}")
        self._set_loading(False)
        # The worker is about to be deleteLater'd via its finished signal,
        # so drop our reference to avoid touching a deleted C++ object.
        self._loader = None
        # Re-apply the current search so a load during an active filter
        # keeps the view filtered.
        if self.searchBox.text():
            self.tableView.proxyModel.setFilterFixedString(self.searchBox.text())

    def _on_load_error(self, message):
        self.statusBar().showMessage(f"Error loading domain: {message}")
        print("Error loading domain", message)
        self._set_loading(False)
        self._loader = None

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

        self.tableView = CustomTableView([])
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

    def onListItemDoubleClick(self, qModelIndex):
        # qModelIndex is a proxy-model index; map it back to the source.
        source_index = self.tableView.proxyModel.mapToSource(qModelIndex)
        self.on_open_linked_file(source_index=source_index)

    def onListItemSelect(self, selected):
        """An item in the table has been clicked/selected.

        :param selected: QItemSelection (in proxy-model coordinates) of
            newly-selected cells. Can be empty (e.g. a model reset from
            search/refresh clears the selection without selecting anything
            new), so that case must be handled instead of unconditionally
            indexing into it.
        """
        if selected.isEmpty():
            return

        # topLeft() is a QModelIndex in the proxy model's coordinate space
        # (the view's model is the proxy); top() would only give an int.
        proxy_index = selected.first().topLeft()
        source_index = self.tableView.proxyModel.mapToSource(proxy_index)
        row = self.tableView.tableModel.arraydata[source_index.row()]
        job_details = self.jobs.get(row[0])
        self.textEdit.setHtml(
            f"""
<pre>
{html.escape(job_details)}
</pre>
"""
        )

        self.statusBar().showMessage(row[1])

    def on_open_linked_file(self, source_index=None):
        # Accept a QModelIndex (source) directly when invoked from a
        # double-click; otherwise resolve the current selection, mapping
        # the proxy index back to the source model. QAction.triggered
        # passes a bool, so the toolbar entry is wired through a lambda to
        # avoid that bool being misread as an index.
        if source_index is None or isinstance(source_index, bool):
            selected_indexes = self.tableView.selectionModel().selectedRows()
            if not selected_indexes:
                show_gui_error("No job selected", "Please select a job first!")
                return
            source_index = self.tableView.proxyModel.mapToSource(selected_indexes[0])
        row = self.tableView.tableModel.arraydata[source_index.row()]
        plist_path = row[1]

        if plist_path and Path(plist_path).exists():
            self.start_file(plist_path)
        else:
            show_gui_error(
                "",
                f"There is no associated plist file for job {row[0]} "
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
        # Let an in-flight loader settle so it doesn't emit into a
        # half-destroyed window; the worker has no GUI interaction of its
        # own so this is at most a short wait.
        if self._loader is not None and self._loader.isRunning():
            self._loader.wait(2000)
        self.write_settings()


class CustomTableView(QtWidgets.QTableView):
    def __init__(self, table_data, *args):
        QtWidgets.QTableView.__init__(self, *args)
        self.tableModel = CustomTableModel(table_data, self)

        # The proxy sits between the view and the source model and owns
        # all filtering + sorting, so the source's row order is never
        # mutated (which previously broke the search cache) and selection
        # indexes map cleanly through mapToSource/mapFromSource.
        self.proxyModel = JobFilterProxyModel(self)
        self.proxyModel.setSourceModel(self.tableModel)
        self.proxyModel.setFilterCaseSensitivity(
            QtCore.Qt.CaseSensitivity.CaseInsensitive
        )
        self.proxyModel.setSortCaseSensitivity(
            QtCore.Qt.CaseSensitivity.CaseInsensitive
        )
        self.setModel(self.proxyModel)
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
        # Sorting is handled by the proxy model, not the source.
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
        self.arraydata = datain if datain is not None else []

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

    def set_jobs(self, data):
        """Replace the canonical dataset in one guarded reset. The proxy
        model is wired to this source model, so a reset here automatically
        refreshes the filtered/sorted view too."""
        self.beginResetModel()
        self.arraydata = list(data)
        self.endResetModel()

    def headerData(self, section, orientation, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if (
            role == QtCore.Qt.ItemDataRole.DisplayRole
            and orientation == QtCore.Qt.Orientation.Horizontal
        ):
            return self.header_labels[section]
        return QtCore.QAbstractTableModel.headerData(self, section, orientation, role)

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
