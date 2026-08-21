"""
Cloud Files - a native stand-in for Fusion's Data Panel.

Under Wine the Data Panel renders blank: it is a Chromium view, and Chromium's
GPU process cannot create D3D11 shared handles (CreateSharedHandle returns
E_NOTIMPL in both DXVK and wined3d), so the surface never paints.

Fusion's Python API reaches the same cloud data without touching Chromium, and
command dialogs are native Qt rather than web views. So this add-in walks the
hub/project/folder tree and lets you pick a file from plain dropdowns.

On startup it also writes a scan to cloudbrowser.log next to this file, so the
data path can be verified without opening any UI.
"""

import traceback
import os
import time
import threading

import adsk.core
import adsk.fusion

CMD_ID = 'CloudBrowserOpenCmd'
RESCAN_EVENT_ID = 'CloudBrowserRescanEvent'
CMD_NAME = 'Cloud Files'
CMD_TOOLTIP = 'Browse and open Fusion Team files (replaces the Data Panel)'
PANEL_ID = 'SolidScriptsAddinsPanel'

# Walking every folder of a large hub is slow and the dialog has to stay
# responsive, so the recursion is bounded.
MAX_DEPTH = 6
MAX_FILES = 500

LOG_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'cloudbrowser.log')

# Fusion drops handlers that aren't referenced, so keep them alive here.
_handlers = []

# Populated on demand: project id -> [(display_label, DataFile), ...]
_file_cache = {}
_projects = []


def log(msg):
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write('%s  %s\n' % (time.strftime('%H:%M:%S'), msg))
    except Exception:
        pass



def ensure_loaded(app, project, timeout=20.0):
    """Fusion fetches folder contents asynchronously, and the Data Panel is what
    normally kicks that off. With the panel dead, enumerating straight away fails
    with InternalValidationError. Make the project active and pump the event loop
    until the data arrives."""
    try:
        app.data.activeProject = project
    except Exception as e:
        log('  activeProject failed for %s: %s' % (project.name, e))
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            root = project.rootFolder
            nf = root.dataFiles.count
            nd = root.dataFolders.count
            log('  loaded "%s": %d files, %d folders at root' % (project.name, nf, nd))
            return True
        except Exception as e:
            last = e
            try:
                adsk.doEvents()
            except Exception:
                pass
            time.sleep(0.3)
    log('  timed out loading "%s": %s' % (project.name, last))
    return False



def retry(fn, timeout=10.0, what=''):
    """Every folder's contents load asynchronously, not just the project root.
    Pump the event loop and retry rather than treating the first miss as empty."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            return fn()
        except Exception as e:
            last = e
            try:
                adsk.doEvents()
            except Exception:
                pass
            time.sleep(0.25)
    if what:
        log('  gave up on %s: %s' % (what, last))
    raise last


def collect_files(folder, prefix='', depth=0, out=None):
    """Flatten a DataFolder into (label, DataFile) pairs, depth- and count-capped."""
    if out is None:
        out = []
    if depth > MAX_DEPTH or len(out) >= MAX_FILES:
        return out
    here = prefix or '/'
    try:
        files = retry(lambda: folder.dataFiles, what='files in ' + here)
        n = retry(lambda: files.count, what='file count in ' + here)
        for i in range(n):
            if len(out) >= MAX_FILES:
                return out
            f = files.item(i)
            try:
                # Only Fusion designs can be opened as documents.
                if f.fileExtension and f.fileExtension.lower() not in ('f3d', 'f3z'):
                    continue
            except Exception:
                pass
            out.append((prefix + f.name, f))
    except Exception:
        pass
    try:
        folders = retry(lambda: folder.dataFolders, what='folders in ' + here)
        n = retry(lambda: folders.count, what='folder count in ' + here)
        for i in range(n):
            if len(out) >= MAX_FILES:
                return out
            sub = folders.item(i)
            collect_files(sub, prefix + sub.name + ' / ', depth + 1, out)
    except Exception:
        pass
    return out


def files_for_project(project, app=None):
    key = project.id
    if key not in _file_cache:
        try:
            if app is not None:
                ensure_loaded(app, project)
            _file_cache[key] = collect_files(project.rootFolder)
        except Exception as e:
            log('collect failed for %s: %s' % (project.name, e))
            _file_cache[key] = []
    return _file_cache[key]


def refresh_projects(app):
    """Every project across every hub, as (label, DataProject)."""
    out = []
    try:
        hubs = app.data.dataHubs
        for h in range(hubs.count):
            hub = hubs.item(h)
            try:
                projects = hub.dataProjects
            except Exception as e:
                log('hub %s projects: %s' % (hub.name, e))
                continue
            for p in range(projects.count):
                proj = projects.item(p)
                label = proj.name if hubs.count == 1 else '%s / %s' % (hub.name, proj.name)
                out.append((label, proj))
    except Exception as e:
        log('refresh_projects: %s' % e)
    return out


def fill_files_dropdown(file_input, project, app=None):
    items = file_input.listItems
    items.clear()
    entries = files_for_project(project, app)
    if not entries:
        items.add('(no designs found)', True, '')
        return
    for idx, (label, _f) in enumerate(entries):
        items.add(label, idx == 0, '')


class ExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, app, ui):
        super().__init__()
        self.app = app
        self.ui = ui

    def notify(self, args):
        try:
            inputs = args.command.commandInputs
            proj_in = inputs.itemById('project')
            file_in = inputs.itemById('file')
            if not proj_in.selectedItem or not file_in.selectedItem:
                return
            project = _projects[proj_in.selectedItem.index][1]
            entries = files_for_project(project, self.app)
            i = file_in.selectedItem.index
            if i < 0 or i >= len(entries):
                self.ui.messageBox('That selection is no longer valid. Reopen Cloud Files.')
                return
            label, data_file = entries[i]
            log('opening: %s' % label)
            self.app.documents.open(data_file, True)
        except Exception:
            log('execute failed:\n' + traceback.format_exc())
            self.ui.messageBox('Could not open that file:\n\n' + traceback.format_exc())


class InputChangedHandler(adsk.core.InputChangedEventHandler):
    """Repopulate the file list whenever the project selection changes."""

    def notify(self, args):
        try:
            if args.input.id != 'project':
                return
            inputs = args.inputs
            proj_in = inputs.itemById('project')
            file_in = inputs.itemById('file')
            if not proj_in.selectedItem:
                return
            project = _projects[proj_in.selectedItem.index][1]
            fill_files_dropdown(file_in, project, adsk.core.Application.get())
        except Exception:
            log('input changed failed:\n' + traceback.format_exc())


class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self, app, ui):
        super().__init__()
        self.app = app
        self.ui = ui

    def notify(self, args):
        try:
            global _projects, _file_cache
            cmd = args.command
            cmd.isExecutedWhenPreEmpted = False
            inputs = cmd.commandInputs

            _file_cache = {}
            _projects = refresh_projects(self.app)

            style = adsk.core.DropDownStyles.TextListDropDownStyle
            proj_in = inputs.addDropDownCommandInput('project', 'Project', style)
            file_in = inputs.addDropDownCommandInput('file', 'Design', style)

            if not _projects:
                proj_in.listItems.add('(no projects - are you signed in?)', True, '')
                file_in.listItems.add('-', True, '')
                inputs.addTextBoxCommandInput(
                    'note', '',
                    'No cloud projects were returned. Check that Fusion is signed in.', 2, True)
                return

            for idx, (label, _p) in enumerate(_projects):
                proj_in.listItems.add(label, idx == 0, '')
            fill_files_dropdown(file_in, _projects[0][1], self.app)

            inputs.addTextBoxCommandInput(
                'note', '',
                'Native replacement for the Data Panel. Lists up to %d designs per project.'
                % MAX_FILES, 2, True)

            on_exec = ExecuteHandler(self.app, self.ui)
            cmd.execute.add(on_exec)
            _handlers.append(on_exec)

            on_change = InputChangedHandler()
            cmd.inputChanged.add(on_change)
            _handlers.append(on_change)
        except Exception:
            log('command created failed:\n' + traceback.format_exc())
            self.ui.messageBox('Cloud Files failed to build its dialog:\n\n' + traceback.format_exc())


def startup_scan(app):
    """Log what the data API can see, so it can be verified without opening the UI.
    Only meaningful once Fusion has finished starting - see schedule_rescans."""
    try:
        log('--- startup scan ---')
        hubs = app.data.dataHubs
        log('hubs: %d' % hubs.count)
        total_projects = 0
        for h in range(hubs.count):
            hub = hubs.item(h)
            try:
                projects = hub.dataProjects
                log('  hub "%s": %d projects' % (hub.name, projects.count))
                total_projects += projects.count
                for p in range(min(projects.count, 10)):
                    proj = projects.item(p)
                    ensure_loaded(app, proj)
                    files = collect_files(proj.rootFolder)
                    log('    project "%s": %d designs' % (proj.name, len(files)))
                    for label, _f in files[:40]:
                        log('        %s' % label)
            except Exception as e:
                log('  hub "%s" failed: %s' % (hub.name, e))
        log('scan complete: %d projects total' % total_projects)
    except Exception:
        log('startup scan failed:\n' + traceback.format_exc())




class RescanHandler(adsk.core.CustomEventHandler):
    """Runs on the main thread, fired by a timer. The startup scan happens while
    Fusion is still coming up, which may be too early for the data service, so
    retry a few times once things are warm."""

    def notify(self, args):
        try:
            app = adsk.core.Application.get()
            log('--- deferred scan ---')
            global _file_cache
            _file_cache = {}
            startup_scan(app)
        except Exception:
            log('rescan failed:\n' + traceback.format_exc())


def schedule_rescans(app, delays=(45,)):
    for d in delays:
        threading.Timer(d, lambda: _fire_rescan(app)).start()
    log('rescans scheduled at %s seconds' % (delays,))


def _fire_rescan(app):
    try:
        app.fireCustomEvent(RESCAN_EVENT_ID)
    except Exception as e:
        log('fireCustomEvent failed: %s' % e)


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        log('=== add-in loading ===')

        existing = ui.commandDefinitions.itemById(CMD_ID)
        if existing:
            existing.deleteMe()
        cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_TOOLTIP)

        on_created = CommandCreatedHandler(app, ui)
        cmd_def.commandCreated.add(on_created)
        _handlers.append(on_created)

        panel = ui.allToolbarPanels.itemById(PANEL_ID)
        if panel:
            if not panel.controls.itemById(CMD_ID):
                panel.controls.addCommand(cmd_def)
            log('button added to panel %s' % PANEL_ID)
        else:
            log('panel %s not found; command still callable by name' % PANEL_ID)

        try:
            app.unregisterCustomEvent(RESCAN_EVENT_ID)
        except Exception:
            pass
        rescan_event = app.registerCustomEvent(RESCAN_EVENT_ID)
        on_rescan = RescanHandler()
        rescan_event.add(on_rescan)
        _handlers.append(on_rescan)

        # Scanning at load time always fails - Fusion's data service isn't ready
        # until well after the add-in starts. One deferred scan records what the
        # account holds, purely so cloudbrowser.log is useful for diagnosis.
        schedule_rescans(app, delays=(45,))
        log('=== add-in ready ===')
    except Exception:
        log('run failed:\n' + traceback.format_exc())
        if ui:
            ui.messageBox('Cloud Files add-in failed to load:\n\n' + traceback.format_exc())


def stop(context):
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        panel = ui.allToolbarPanels.itemById(PANEL_ID)
        if panel:
            ctrl = panel.controls.itemById(CMD_ID)
            if ctrl:
                ctrl.deleteMe()
        cmd_def = ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()
        try:
            app.unregisterCustomEvent(RESCAN_EVENT_ID)
        except Exception:
            pass
        log('=== add-in stopped ===')
    except Exception:
        log('stop failed:\n' + traceback.format_exc())
