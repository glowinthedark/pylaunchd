#!/usr/bin/env python3
# -*- coding: ascii -*-
import operator
import os
import re
import subprocess
import sys
from pathlib import Path

# import launchd
from PyQt5 import QtCore
from PyQt5 import QtGui
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QLineEdit

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


TODO:
- search by label/path 
- add DOMAINS dropdown:
    - system (launchctl print system)
    - user (launchctl print user/`id -u`)
    - gui (launchctl print gui/`id -u`)

- get service info
launchctl print gui/501/yanue.v2rayu.v2ray-core
                    ^^ = id -u

- Load (DEPRECATED)
launchctl load -w ~/Library/LaunchAgents/some.plist

- Unload (DEPRECATED)
launchctl unload -w ~/Library/LaunchAgents/some.plist

- NEW WAY (requires target domain + uid = `id -u`, except for 'system')
> load
launchctl bootstrap gui/UID some.plist

> unload
launchctl bootout gui/UID some.plist

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

APPNAME = 'pyLaunchd'
VERSION = '22.2221 (pyqt5)'

LAUNCHD_DOMAINS = ["User", "System", "GUI"]
DEFAULT_DOMAIN = LAUNCHD_DOMAINS[2]

DEFAULT_EDITOR = 'system'


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()

        self.actionLogToFileEnabled = None
        self.is_toolbar_hidden = False
        self.read_settings()

        self.iconSwitch = self.style().standardIcon(QtWidgets.QStyle.SP_MediaSkipForward)
        self.setWindowIcon(self.iconSwitch)

        self.setGeometry(100, 150, 500, 660)

        self.jobs = {}
        self.createActions()
        self.createMenus()
        self.createToolBars()
        # TODO: get id from saved settings (INI file)
        self.data = self.load_data_launchctl(self.domain_id)
        self.data_all = []
        self.data_all[:] = self.data
        self.statusBar().showMessage(f'Total jobs: {len(self.data)}')

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
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = process.communicate()
        result = out.decode('utf-8')
        if err:
            show_gui_error(str(args), err.decode('utf-8'))
        return result

    def initialize_data(self, idx=0):
        try:
            self.tableView.tableModel.sendSignalLayoutAboutToBeChanged()
            self.data[:] = self.load_data_launchctl(idx)
            self.data_all[:] = self.data
            self.tableView.tableModel.sendSignalLayoutChanged()
        except Exception as e:
            print("Error initializing data", e)

    def on_about(self):
        QtWidgets.QMessageBox.about(self, "Abouts",
                                    "%s<br/><br/>"
                                    "Version: %s<br/><br/>"
                                    "From: <a href='mailto:slavery.two.point.zero@gmail.com'>slavery.two.point.zero@gmail.com</a><br/><br/>"
                                    "Subject: For a moment, nothing happened.&nbsp;Then, after a second or so, nothing continued to happen...<br/><br/>"
                                    "Ponty Mython Podructions<br>Drain Bamage Season 2<br>&copy; 2022" % (
                                        APPNAME, VERSION))

    def run_job_action(self, args):
        selected_indexes = self.tableView.selectionModel().selectedRows()

        if len(selected_indexes):
            idx = selected_indexes[0].row()
            label = self.data[idx][0]
            result = self.exec(args + [label])
            if result:
                self.statusBar().showMessage(result)
        else:
            show_gui_error("Please select a job first!")

    def on_start_job(self, which):
        self.run_job_action(['launchctl', 'start'])

    def on_stop_job(self, which):
        self.run_job_action(['launchctl', 'stop'])

    def on_enable_job(self, which):
        self.run_job_action(['launchctl', 'enable'])

    def on_disable_job(self, which):
        self.run_job_action(['launchctl', 'disable'])

    def on_show_in_finder(self, which):
        selected_indexes = self.tableView.selectionModel().selectedRows()

        if len(selected_indexes):
            idx = selected_indexes[0].row()
            path = self.data[idx][1]
            result = self.exec(['open', '-R', path])
            if result:
                self.statusBar().showMessage(result)
        else:
            show_gui_error("Please select a job first!")

    def on_refresh(self, which):
        domain_index = self.comboBoxDomain.currentIndex()
        self.statusBar().showMessage(f'Refreshing domain {LAUNCHD_DOMAINS[domain_index]} - please wait...')
        self.initialize_data(domain_index)
        self.statusBar().showMessage(f'Total jobs: {len(self.data)}')

    def createActions(self):

        self.actionOpenFile = QtWidgets.QAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_ArrowRight),
            "&Open...", self,
            shortcut=QtGui.QKeySequence.Forward,
            statusTip="Open associated plist file",
            triggered=self.on_open_linked_file)

        self.actionToggleToolbar = QtWidgets.QAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_DialogCloseButton),
            "Hide toolbar...", self,
            shortcut=QtGui.QKeySequence.Bold,
            statusTip="Show or hide toolbar",
            triggered=self.on_toggle_toolbar)

        self.actionSetEditor = QtWidgets.QAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogDetailedView),
            "Set editor...", self,
            statusTip="Set editor app for viewing plist files",
            triggered=self.on_editor_config,
            checkable=True)

        self.actionQuit = QtWidgets.QAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_BrowserStop),
            "&Quit",
            self,
            statusTip="Quit the application", triggered=self.close)

        self.actionAbout = QtWidgets.QAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogInfoView),
            "About", self,
            statusTip="Show the About box",
            triggered=self.on_about)

        self.actionStart = QtWidgets.QAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_MediaPlay),
            "Start", self,
            statusTip="Start job",
            triggered=self.on_start_job)

        self.actionStop = QtWidgets.QAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_MediaStop),
            "Stop", self,
            statusTip="Stop job",
            triggered=self.on_stop_job)

        self.actionEnable = QtWidgets.QAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_DialogApplyButton),
            "Enable", self,
            statusTip="Enable job",
            triggered=self.on_enable_job)

        self.actionDisable = QtWidgets.QAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_DialogCancelButton),
            "Disable", self,
            statusTip="Disable job",
            triggered=self.on_disable_job)

        self.actionShowInFinder = QtWidgets.QAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_DirIcon),
            "Show in Finder", self,
            statusTip="Show plist file in finder",
            triggered=self.on_show_in_finder)

        self.actionRefresh = QtWidgets.QAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_BrowserReload),
            "Refresh", self,
            statusTip="Refresh",
            triggered=self.on_refresh)

    def on_toggle_toolbar(self):
        self.is_toolbar_hidden = self.actionToggleToolbar.isChecked()
        self.toolBar.setVisible(not self.is_toolbar_hidden)

    def on_editor_config(self):
        value, ok = QtWidgets.QInputDialog.getText(self,
                                                   "Configure Editor",
                                                   "Editor name or command line",
                                                   QLineEdit.Normal,
                                                   self.editor)
        if ok:
            self.editor = value
            self.statusBar().showMessage(f'Editor="{self.editor}"')

    def on_domain_changed(self, selected_index):
        # self.load_data_launchctl(selected_index)
        self.domain_id = selected_index
        self.statusBar().showMessage(f'Loading jobs for domain [{LAUNCHD_DOMAINS[selected_index]}] - please wait...')
        self.initialize_data(selected_index)
        self.statusBar().showMessage(f'Total jobs: {len(self.data)}')
        # self.data = self.load_data_launchctl(selected_index)[:]

    def on_search_changed(self, text):

        if text:
            self.statusBar().showMessage(f'Filter by: {text}')

        try:
            self.tableView.tableModel.sendSignalLayoutAboutToBeChanged()
            if text:
                filtered_data = [d for d in self.data_all if text.lower() in d[0].lower() or text in d[1].lower()]
            else:
                filtered_data = self.data_all
            self.data[:] = filtered_data
            self.tableView.tableModel.sendSignalLayoutChanged()
        except Exception as e:
            self.statusBar().showMessage(str(e))
            print("Error initializing data", e)

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
        self.toolBar.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
        self.comboBoxDomain = QtWidgets.QComboBox()
        self.comboBoxDomain.insertItems(1, LAUNCHD_DOMAINS)
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
        self.searchBox = QLineEdit(self, placeholderText='search...')
        self.searchBox.textChanged.connect(self.on_search_changed)
        self.toolBar.addWidget(self.searchBox)

    def load_data_launchctl(self, domain_id=0):
        data = []
        uid = os.getuid()

        domain = LAUNCHD_DOMAINS[domain_id].lower() or DEFAULT_DOMAIN

        if domain == 'system':
            user_identifier = ''
        else:
            user_identifier = f'/{uid}'

        gui_processes = self.exec(['launchctl', 'print', f'{domain}{user_identifier}'])

        services = gui_processes.split('services = {\n')[1].split('\t}')[0]

        for line in services.splitlines():
            label = line.split('\t')[-1]
            if label:
                details = self.exec(['launchctl', 'print', f'{domain}{user_identifier}/{label}'])
                self.jobs[label] = details
                paths = re.findall('^\s+path =\s(.*$)', details, re.MULTILINE)
                path = len(paths) and paths[0] or None

                if path and path.startswith('/'):
                    states = re.findall('^\s+state =\s(.*$)', details, re.MULTILINE)
                    state = len(states) and states[0] or ''
                    data.append([label, path, state])

        return data


    def createDockWindows(self):
        self.topDock = QtWidgets.QDockWidget(self)
        self.topDock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFloatable)
        self.topDock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea
                                     | QtCore.Qt.RightDockWidgetArea
                                     | QtCore.Qt.TopDockWidgetArea
                                     | QtCore.Qt.BottomDockWidgetArea)

        self.tableView = CustomTableView(self.data)
        self.tableView.addAction(self.actionOpenFile)
        self.tableView.addAction(self.actionStart)
        self.tableView.addAction(self.actionStop)
        self.tableView.addAction(self.actionEnable)
        self.tableView.addAction(self.actionDisable)
        self.tableView.addAction(self.actionShowInFinder)
        self.tableView.setAppWindowHandle(self)

        self.tableView.setSelectionMode(QtWidgets.QTableView.SingleSelection)
        self.topDock.setWindowTitle("registered services")

        self.topDock.setWidget(self.tableView)
        self.tableView.selectionModel().selectionChanged.connect(self.onListItemSelect)
        self.tableView.doubleClicked.connect(self.onListItemDoubleClick)

        self.addDockWidget(QtCore.Qt.TopDockWidgetArea, self.topDock)

        self.bottomDock = QtWidgets.QDockWidget("", self)
        self.bottomDock.setFeatures(QtWidgets.QDockWidget.DockWidgetVerticalTitleBar)
        self.bottomDock.setTitleBarWidget(QtWidgets.QWidget(self.bottomDock))
        # self.bottomDock.setFeatures(QtWidgets.QDockWidget.DockWidgetClosable)
        self.bottomDock.setAllowedAreas(QtCore.Qt.BottomDockWidgetArea | QtCore.Qt.RightDockWidgetArea)

        ## hide initially
        self.bottomDock.hide()
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.bottomDock)

    # def restore_column_sort_mode(self):
    #     if len(tableview_data):
    #         if self.last_saved_sort_column and self.last_saved_sort_order:
    #             self.tableView.tableModel.sort(self.last_saved_sort_column, self.last_saved_sort_order)

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
        """an item in the listbox has been clicked/selected
        :param selected bool
        """
        rowIndex = selected.first().top()
        row_data = self.data[rowIndex]
        job_details = self.jobs.get(row_data[0])
        self.textEdit.setHtml(
            f'''
<pre>
{job_details}
</pre>
''')

        self.statusBar().showMessage(row_data[1])

    def on_open_linked_file(self, row_index=None):
        if not row_index:
            selected_indexes = self.tableView.selectionModel().selectedRows()

            if len(selected_indexes):
                row_index = selected_indexes[0].row()
            else:
                show_gui_error("No message selected", "Please select a message first!")
                return
        plist_path = self.data[row_index][1]

        if plist_path and Path(plist_path).exists():
            self.start_file(plist_path)
        else:
            show_gui_error("", f"There is no associated plist file for job {self.data[row_index][0]} "
                               f"\nor invalid path [{plist_path}]")

    def start_file(self, filepath):
        if not self.editor:
            self.editor = 'system'

        if self.editor == 'system':
            self.exec(('open', filepath))
        else:
            if self.editor.startswith('/'):
                self.exec((self.editor, filepath))
            elif '-' in self.editor:
                self.exec(self.editor.split() + [filepath])
            else:
                self.exec(('open', '-a', self.editor, filepath))

    def read_settings(self):
        self.settings = QtCore.QSettings(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, "xh", APPNAME)
        pos = self.settings.value("pos", QtCore.QPoint(200, 200))
        size = self.settings.value("size", QtCore.QSize(600, 400))
        self.resize(size)
        self.move(pos)
        self.last_saved_sort_column = self.settings.contains('last_saved_sort_column') and self.settings.value(
            "last_saved_sort_column", type=int) or None
        self.last_saved_sort_order = self.settings.contains('last_saved_sort_order') and self.settings.value(
            "last_saved_sort_order", type=int) or None
        self.domain_id = self.settings.contains('domain_id') and self.settings.value(
            "domain_id", type=int) or 0
        self.is_toolbar_hidden = self.settings.contains('is_toolbar_hidden') and self.settings.value(
            "is_toolbar_hidden", type=bool) or False
        self.editor = self.settings.contains('editor') and self.settings.value(
            "editor", type=str) or DEFAULT_EDITOR

    def write_settings(self):
        settings = QtCore.QSettings(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, "xh", APPNAME)
        settings.setValue("pos", self.pos())
        settings.setValue("size", self.size())
        # settings.setValue("last_saved_sort_column", self.last_saved_sort_column)
        # settings.setValue("last_saved_sort_order", self.last_saved_sort_order)
        settings.setValue("is_toolbar_hidden", self.is_toolbar_hidden)
        settings.setValue("editor", self.editor)
        settings.setValue("domain_id", self.domain_id)
        settings.sync()

    def closeEvent(self, event):

        self.write_settings()

        return
        quit_msg = "Are you sure you want to exit the program?"
        reply = QtWidgets.QMessageBox.question(self, 'Message',
                                               quit_msg, QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.No)

        if reply == QtWidgets.QMessageBox.Yes:
            self.write_settings()
            event.accept()
            QtWidgets.QApplication.instance().quit()
        else:
            event.ignore()


class CustomTableView(QtWidgets.QTableView):
    def __init__(self, table_data, *args):
        QtWidgets.QTableView.__init__(self, *args)
        self.tableModel = CustomTableModel(table_data, self)
        self.setModel(self.tableModel)
        self.setContextMenuPolicy(QtCore.Qt.ActionsContextMenu)

    def setAppWindowHandle(self, mainWindowHandle):
        self.mainWindow = mainWindowHandle

    def configureTableView(self):
        self.setShowGrid(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setTabKeyNavigation(False)

        # disable row editing
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

        # disable bold column headers
        horizontalHeader = self.horizontalHeader()
        horizontalHeader.setHighlightSections(False)

        self.style().pixelMetric(QtWidgets.QStyle.PM_ScrollBarExtent)
        self.setWordWrap(True)
        self.setSortingEnabled(True)
        self.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustToContents)
        self.resizeColumnsToContents()


# http://www.saltycrane.com/blog/2007/06/pyqt-42-qabstracttablemodelqtableview/
class CustomTableModel(QtCore.QAbstractTableModel):
    header_labels = ['Label', 'Path', 'State']

    def __init__(self, datain, parent=None, *args):
        QtCore.QAbstractTableModel.__init__(self, parent, *args)
        self.arraydata = datain
        self.last_saved_sort_column = None
        self.last_saved_sort_order = None

    def rowCount(self, parent):
        return len(self.arraydata)

    def columnCount(self, parent):
        return len(self.header_labels)

    def data(self, qModelIndex, role):
        # index is a QModelIndex type
        if not qModelIndex.isValid():
            return QtCore.QVariant()
        elif role != QtCore.Qt.DisplayRole:
            return QtCore.QVariant()
        elif role == QtCore.Qt.TextAlignmentRole:
            return QtCore.Qt.AlignLeft
        elif qModelIndex.isValid() and role == QtCore.Qt.DecorationRole:
            row = qModelIndex.row()
            column = qModelIndex.column()
            value = None
            try:
                value = self.arraydata[row][column]
            except IndexError:
                return
        elif qModelIndex.isValid() and role == QtCore.Qt.DisplayRole:
            row = qModelIndex.row()
            column = qModelIndex.column()
            try:
                value = self.arraydata[row][column]
            except IndexError:
                return
            return value

        return QtCore.QVariant(self.arraydata[qModelIndex.row()][qModelIndex.column()])

    def setData(self, index, value, role=QtCore.Qt.EditRole):
        if role == QtCore.Qt.EditRole:
            self.arraydata[index.row()] = value
            self.dataChanged.emit(index, index)
            return True
        return False

    def sendSignalLayoutAboutToBeChanged(self):
        self.layoutAboutToBeChanged.emit()
        self.beginResetModel()

    def sendSignalLayoutChanged(self):
        self.endResetModel()
        self.layoutChanged.emit()

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole and orientation == QtCore.Qt.Horizontal:
            return self.header_labels[section]
        return QtCore.QAbstractTableModel.headerData(self, section, orientation, role)

    def insertRows(self, position, item, parent=QtCore.QModelIndex()):

        self.beginInsertRows(QtCore.QModelIndex(), len(self.arraydata), len(self.arraydata) + 1)
        self.arraydata.append(item)  # Item must be an array
        self.endInsertRows()
        return True

    def sort(self, ncol, order):
        """
        Sort table by given column number.
        """
        self.sendSignalLayoutAboutToBeChanged()

        sorted_data = sorted(self.arraydata, key=operator.itemgetter(ncol), reverse=order)
        self.arraydata[:] = sorted_data[:]
        self.sendSignalLayoutChanged()
        self.last_saved_sort_column = ncol
        self.last_saved_sort_order = order

    def flags(self, index):
        return QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsSelectable


def show_gui_error(msg, error_text=''):
    QtWidgets.QMessageBox.warning(None, APPNAME, msg + error_text and ('\n\n' + error_text) or '')


def main():
    app = QtWidgets.QApplication(sys.argv)
    mainWin = MainWindow()
    mainWin.show()
    mainWin.raise_()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
