# -*- coding: utf-8 -*-
# Copyright 2004-2005 Joe Wreschnig, Michael Urman, Iñigo Serna
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation
#
# $Id$

import os, sys
import sre
import time     # ~#lastplayed display
import shutil   # renames (and Move to Trash)
import dircache # Ex Falso directory display

import gtk, pango, gobject
import qltk

import const
import config
import player
import parser
import formats
import util

from util import to
from library import library

if sys.version_info < (2, 4):
    from sets import Set as set

# Give us a namespace for now.. FIXME: We need to remove this later.
# Or, replace it with nicer wrappers!
class widgets(object): pass

# Provides some files in ~/.quodlibet to indicate what song is playing
# and whether the player is paused or not.
class FSInterface(object):
    def __init__(self, watcher):
        watcher.connect('paused', self.__paused)
        watcher.connect('unpaused', self.__unpaused)
        watcher.connect('song-started', self.__started)
        watcher.connect('song-ended', self.__ended)

    def __paused(self, watcher):
        try: file(const.PAUSED, "w").close()
        except EnvironmentError: pass

    def __unpaused(self, watcher):
        try: os.unlink(const.PAUSED)
        except EnvironmentError: pass

    def __started(self, watcher, song):
        if song:
            try: f = file(const.CURRENT, "w")
            except EnvironmentError: pass
            else:
                f.write(song.to_dump())
                f.close()

    def __ended(self, watcher, song, stopped):
        try: os.unlink(const.CURRENT)
        except EnvironmentError: pass

# Make a standard directory-chooser, and return the filenames and response.
class FileChooser(gtk.FileChooserDialog):
    def __init__(self, parent, title, initial_dir=None):
        super(FileChooser, self).__init__(
            title=title, parent=parent,
            action=gtk.FILE_CHOOSER_ACTION_SELECT_FOLDER,
            buttons=(gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                     gtk.STOCK_OPEN, gtk.RESPONSE_OK))
        if initial_dir: self.set_current_folder(initial_dir)
        self.set_local_only(True)
        self.set_select_multiple(True)

    def run(self):
        resp = gtk.FileChooserDialog.run(self)
        fns = self.get_filenames()
        return resp, fns

# Everything connects to this to get updates about the library and player.
class SongWatcher(gtk.Object):
    SIG_PYOBJECT = (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, (object,))
    SIG_NONE = (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ())
    
    __gsignals__ = {
        # A song in the library has been changed; update it in all views.
        'changed': SIG_PYOBJECT,

        # A song was removed from the library; remove it from all views.
        'removed': SIG_PYOBJECT,

        # A song was added to the library.
        'added': SIG_PYOBJECT,

        # A group of changes has been finished; all library views should
        # do a global refresh if necessary
        'refresh': SIG_NONE,

        # A new song started playing (or the current one was restarted).
        'song-started': SIG_PYOBJECT,

        # The song was seeked within.
        'seek': (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE,
                 (object, int)),

        # A new song started playing (or the current one was restarted).
        # The boolean is True if the song was stopped rather than simply
        # ended.
        'song-ended': (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE,
                       (object, bool)),

        # Playback was paused.
        'paused': SIG_NONE,

        # Playback was unpaused.
        'unpaused': SIG_NONE,

        # A song was missing (i.e. disappeared from the filesystem).
        # When QL is running it will also result in a removed signal
        # (caused by MainWindow).
        'missing': SIG_PYOBJECT
        }

    # (current_in_msec, total_in_msec)
    # (0, 1) when no song is playing.
    time = (0, 1)

    # the currently playing song.
    song = None

    def changed(self, song):
        gobject.idle_add(self.emit, 'changed', song)

    def added(self, song):
        gobject.idle_add(self.emit, 'added', song)

    def removed(self, song):
        gobject.idle_add(self.emit, 'removed', song)

    def missing(self, song):
        gobject.idle_add(self.emit, 'missing', song)

    def song_started(self, song):
        if song: self.time = (0, song["~#length"] * 1000)
        else: self.time = (0, 1)
        self.song = song
        gobject.idle_add(self.emit, 'song-started', song)

    def song_ended(self, song, stopped):
        self.changed(song)
        gobject.idle_add(self.emit, 'song-ended', song, stopped)

    def refresh(self):
        gobject.idle_add(self.emit, 'refresh')

    def set_paused(self, paused):
        if paused: gobject.idle_add(self.emit, 'paused')
        else: gobject.idle_add(self.emit, 'unpaused')

    def seek(self, song, position_in_msec):
        gobject.idle_add(self.emit, 'seek', song, position_in_msec)

    def error(self, song):
        try: song.reload()
        except Exception, err:
            sys.stdout.write(str(err) + "\n")
            library.remove(song)
            self.removed(song)
        else: self.changed(song)

gobject.type_register(SongWatcher)

class AboutWindow(gtk.AboutDialog):
    def __init__(self, parent=None):
        gtk.AboutDialog.__init__(self)
        self.set_name("Quod Libet")
        self.set_version(const.VERSION)
        self.set_authors(const.AUTHORS)
        self.set_comments(_("An audio player and tag editor"))
        # Translators: Replace this with your name/email to have it appear
        # in the "About" dialog.
        self.set_translator_credits(_('translator-credits'))
        # The icon looks pretty ugly at this size.
        #self.set_logo_icon_name(const.ICON)
        self.set_website("http://www.sacredchao.net/quodlibet")
        self.set_copyright(
            "Copyright © 2004-2005 Joe Wreschnig, Michael Urman, & others\n"
            "<quodlibet@lists.sacredchao.net>")
        icon_theme = gtk.icon_theme_get_default()
        self.set_icon(icon_theme.load_icon(const.ICON, 64,
            gtk.ICON_LOOKUP_USE_BUILTIN))
        gtk.AboutDialog.run(self)
        self.destroy()

class PreferencesWindow(gtk.Window):
    class _Pane(object):
        def _toggle(self, c, name, section="settings"):
            config.set(section, name, str(bool(c.get_active())))

        def _changed(self, cb, name):
            config.set("settings", name, str(cb.get_active()))

    class SongList(_Pane, gtk.VBox):
        def __init__(self):
            gtk.VBox.__init__(self, spacing=12)
            self.set_border_width(12)
            self.title = _("Song List")
            vbox = gtk.VBox(spacing=12)
            tips = gtk.Tooltips()

            c = qltk.ConfigCheckButton(
                _("_Jump to current song automatically"), 'settings', 'jump')
            tips.set_tip(c, _("When the playing song changes, "
                              "scroll to it in the song list"))
            c.set_active(config.state("jump"))
            self.pack_start(c, expand=False)

            buttons = {}
            table = gtk.Table(3, 3)
            table.set_homogeneous(True)
            checks = config.get("settings", "headers").split()
            for j, l in enumerate(
                [[("~#disc", _("_Disc")),
                  ("album", _("Al_bum")),
                  ("genre", _("_Genre"))],
                 [("~#track", _("_Track")),
                  ("artist", _("A_rtist")),
                  ("~basename",_("_Filename"))],
                 [("title", tag("title")),
                  ("date", _("Dat_e")),
                  ("~length",_("_Length"))]]):
                for i, (k, t) in enumerate(l):
                    buttons[k] = gtk.CheckButton(t)
                    if k in checks:
                        buttons[k].set_active(True)
                        checks.remove(k)

                    table.attach(buttons[k], i, i + 1, j, j + 1)

            vbox.pack_start(table, expand=False)

            vbox2 = gtk.VBox()
            rat = gtk.CheckButton(_("Display song _rating"))
            if "~rating" in checks:
                rat.set_active(True)
                checks.remove("~rating")
            tiv = gtk.CheckButton(_("Title includes _version"))
            if "~title~version" in checks:
                buttons["title"].set_active(True)
                tiv.set_active(True)
                checks.remove("~title~version")
            aip = gtk.CheckButton(_("Album includes _part"))
            if "~album~part" in checks:
                buttons["album"].set_active(True)
                aip.set_active(True)
                checks.remove("~album~part")
            fip = gtk.CheckButton(_("Filename includes _folder"))
            if "~filename" in checks:
                buttons["~basename"].set_active(True)
                fip.set_active(True)
                checks.remove("~filename")

            t = gtk.Table(2, 2)
            t.set_homogeneous(True)
            t.attach(tiv, 0, 1, 0, 1)
            t.attach(aip, 0, 1, 1, 2)
            t.attach(fip, 1, 2, 0, 1)
            t.attach(rat, 1, 2, 1, 2)
            vbox.pack_start(t, expand=False)

            hbox = gtk.HBox(spacing=6)
            l = gtk.Label(_("_Others:"))
            hbox.pack_start(l, expand=False)
            others = gtk.Entry()
            if "~current" in checks: checks.remove("~current")
            others.set_text(" ".join(checks))
            tips.set_tip(others, _("List other headers you want displayed, "
                                   "separated by spaces"))
            l.set_mnemonic_widget(others)
            l.set_use_underline(True)
            hbox.pack_start(others)
            vbox.pack_start(hbox, expand=False)

            apply = gtk.Button(stock=gtk.STOCK_APPLY)
            apply.connect(
                'clicked', self.__apply, buttons, rat, tiv, aip, fip, others)
            b = gtk.HButtonBox()
            b.set_layout(gtk.BUTTONBOX_END)
            b.pack_start(apply)
            vbox.pack_start(b)

            frame = qltk.Frame(_("Visible Columns"), bold=True, child=vbox)
            self.pack_start(frame, expand=False)
            self.show_all()

        def __apply(self, button, buttons, rat, tiv, aip, fip, others):
            headers = []
            for key in ["~#disc", "~#track", "title", "album", "artist",
                        "date", "genre", "~basename", "~length"]:
                if buttons[key].get_active(): headers.append(key)
            if rat.get_active():
                if headers and headers[-1] == "~length":
                    headers.insert(-1, "~rating")
                else: headers.append("~rating")
            if tiv.get_active():
                try: headers[headers.index("title")] = "~title~version"
                except ValueError: pass
            if aip.get_active():
                try: headers[headers.index("album")] = "~album~part"
                except ValueError: pass
            if fip.get_active():
                try: headers[headers.index("~basename")] = "~filename"
                except ValueError: pass

            headers.extend(others.get_text().split())
            if "~current" in headers: headers.remove("~current")
            headers.insert(0, "~current")
            config.set("settings", "headers", " ".join(headers))
            SongList.set_all_column_headers(headers)

    class Browsers(_Pane, gtk.VBox):
        def __init__(self):
            gtk.VBox.__init__(self, spacing=12)
            self.set_border_width(12)
            self.title = _("Browsers")
            tips = gtk.Tooltips()
            c = qltk.ConfigCheckButton(
                _("Color _search terms"), 'browsers', 'color')
            c.set_active(config.getboolean("browsers", "color"))
            tips.set_tip(
                c, _("Display simple searches in blue, "
                     "advanced ones in green, and invalid ones in red"))
                         
            hb = gtk.HBox(spacing=6)
            l = gtk.Label(_("_Global filter:"))
            l.set_use_underline(True)
            e = qltk.ValidatingEntry(parser.is_valid_color)
            e.set_text(config.get("browsers", "background"))
            e.connect('changed', self._entry, 'background', 'browsers')
            l.set_mnemonic_widget(e)
            hb.pack_start(l, expand=False)
            hb.pack_start(e)
            self.pack_start(hb, expand=False)

            f = qltk.Frame(_("Search Bar"), bold=True, child=c)
            self.pack_start(f, expand=False)

            t = gtk.Table(2, 4)
            t.set_col_spacings(3)
            t.set_row_spacings(3)
            cbes = [gtk.combo_box_entry_new_text() for i in range(3)]
            values = ["", "genre", "artist", "album"]
            current = config.get("browsers", "panes").split()
            for i, c in enumerate(cbes):
                for v in values:
                    c.append_text(v)
                    try: c.child.set_text(current[i])
                    except IndexError: pass
                lbl = gtk.Label(_("_Pane %d:") % (i + 1))
                lbl.set_use_underline(True)
                lbl.set_mnemonic_widget(c)
                t.attach(lbl, 0, 1, i, i + 1, xoptions=0)
                t.attach(c, 1, 2, i, i + 1)
            b = gtk.Button(stock=gtk.STOCK_APPLY)
            b.connect('clicked', self.__update_panes, cbes)
            bbox = gtk.HButtonBox()
            bbox.set_layout(gtk.BUTTONBOX_END)
            bbox.pack_start(b)
            t.attach(bbox, 0, 2, 3, 4)
            self.pack_start(
                qltk.Frame(_("Paned Browser"), bold=True, child=t),
                expand=False)
            self.show_all()

        def _entry(self, entry, name, section="settings"):
            config.set(section, name, entry.get_text())

        def __update_panes(self, button, cbes):
            panes = " ".join([c.child.get_text() for c in cbes])
            config.set('browsers', 'panes', panes)
            if hasattr(widgets.main.browser, 'refresh_panes'):
                widgets.main.browser.refresh_panes(restore=True)

    class Player(_Pane, gtk.VBox):
        def __init__(self):
            gtk.VBox.__init__(self, spacing=12)
            self.set_border_width(12)
            self.title = _("Player")
            tips = gtk.Tooltips()
            vbox = gtk.VBox()
            c = qltk.ConfigCheckButton(
                _("Show _album cover images"), 'settings', 'cover')
            c.set_active(config.state("cover"))
            c.connect('toggled', self.__toggle_cover)
            vbox.pack_start(c)

            c = gtk.CheckButton(_("Closing _minimizes to system tray"))
            c.set_sensitive(widgets.main.icon.enabled)
            c.set_active(config.getboolean('plugins', 'icon_close') and
                         c.get_property('sensitive'))
            c.connect('toggled', self._toggle, 'icon_close', 'plugins')
            vbox.pack_start(c)
            self.pack_start(vbox, expand=False)

            f = qltk.Frame(_("_Volume Normalization"), bold=True)
            cb = gtk.combo_box_new_text()
            cb.append_text(_("No volume adjustment"))
            cb.append_text(_('Per-song ("Radio") volume adjustment'))
            cb.append_text(_('Per-album ("Audiophile") volume adjustment'))
            f.get_label_widget().set_mnemonic_widget(cb)
            f.child.add(cb)
            cb.set_active(config.getint("settings", "gain"))
            cb.connect('changed', self._changed, 'gain')
            self.pack_start(f, expand=False)

            f = qltk.Frame(_("_On-Screen Display"), bold=True)
            cb = gtk.combo_box_new_text()
            cb.append_text(_("No on-screen display"))
            cb.append_text(_('Display OSD on the top'))
            cb.append_text(_('Display OSD on the bottom'))
            cb.set_active(config.getint('settings', 'osd'))
            cb.connect('changed', self._changed, 'osd')
            f.get_label_widget().set_mnemonic_widget(cb)
            vbox = gtk.VBox(spacing=6)
            f.child.add(vbox)
            f.child.child.pack_start(cb, expand=False)
            hb = gtk.HBox(spacing=6)
            c1, c2 = config.get("settings", "osdcolors").split()
            color1 = gtk.ColorButton(gtk.gdk.color_parse(c1))
            color2 = gtk.ColorButton(gtk.gdk.color_parse(c2))
            tips.set_tip(color1, _("Select a color for the OSD"))
            tips.set_tip(color2, _("Select a second color for the OSD"))
            color1.connect('color-set', self.__color_set, color1, color2)
            color2.connect('color-set', self.__color_set, color1, color2)
            font = gtk.FontButton(config.get("settings", "osdfont"))
            font.connect('font-set', self.__font_set)
            hb.pack_start(color1, expand=False)
            hb.pack_start(color2, expand=False)
            hb.pack_start(font)
            vbox.pack_start(hb, expand=False)
            self.pack_start(f, expand=False)
            self.show_all()

        def __font_set(self, font):
            config.set("settings", "osdfont", font.get_font_name())

        def __color_set(self, color, c1, c2):
            color = c1.get_color()
            ct1 = (color.red // 256, color.green // 256, color.blue // 256)
            color = c2.get_color()
            ct2 = (color.red // 256, color.green // 256, color.blue // 256)
            config.set("settings", "osdcolors",
                       "#%02x%02x%02x #%02x%02x%02x" % (ct1+ct2))

        def __toggle_cover(self, c):
            if config.state("cover"): widgets.main.image.show()
            else: widgets.main.image.hide()

    class Library(_Pane, gtk.VBox):
        def __init__(self):
            gtk.VBox.__init__(self, spacing=12)
            self.set_border_width(12)
            self.title = _("Library")
            f = qltk.Frame(_("Scan _Directories"), bold=True)
            hb = gtk.HBox(spacing=6)
            b = qltk.Button(_("_Select"), gtk.STOCK_OPEN)
            e = gtk.Entry()
            e.set_text(util.fsdecode(config.get("settings", "scan")))
            f.get_label_widget().set_mnemonic_widget(e)
            hb.pack_start(e)
            tips = gtk.Tooltips()
            tips.set_tip(e, _("On start up, any files found in these "
                              "directories will be added to your library"))
            hb.pack_start(b, expand=False)
            b.connect('clicked', self.__select, e, const.HOME)
            e.connect('changed', self._changed, 'scan')
            f.child.add(hb)
            self.pack_start(f, expand=False)

            f = qltk.Frame(_("_Masked Directories"), bold=True)
            vb = gtk.VBox(spacing=6)
            l = gtk.Label(_(
                "If you have songs in directories that will not always be "
                "mounted (for example, a removable device or an NFS shared "
                "drive), list those mount points here. Files in these "
                "directories will not be removed from the library if the "
                "device is not mounted."))
            l.set_line_wrap(True)
            l.set_justify(gtk.JUSTIFY_FILL)
            vb.pack_start(l, expand=False)
            hb = gtk.HBox(spacing=6)
            b = qltk.Button(_("_Select"), gtk.STOCK_OPEN)
            e = gtk.Entry()
            e.set_text(util.fsdecode(config.get("settings", "masked")))
            f.get_label_widget().set_mnemonic_widget(e)
            hb.pack_start(e)
            hb.pack_start(b, expand=False)
            vb.pack_start(hb, expand=False)
            if os.path.exists("/media"): dir = "/media"
            elif os.path.exists("/mnt"): dir = "/mnt"
            else: dir = "/"
            b.connect('clicked', self.__select, e, dir)
            e.connect('changed', self._changed, 'masked')
            f.child.add(vb)
            self.pack_start(f, expand=False)

            f = qltk.Frame(_("Tag Editing"), bold=True)
            vbox = gtk.VBox(spacing=6)
            hb = gtk.HBox(spacing=6)
            e = gtk.Entry()
            e.set_text(config.get("settings", "splitters"))
            e.connect('changed', self._changed, 'splitters')
            tips.set_tip(
                e, _('These characters will be used as separators '
                     'when "Split values" is selected in the tag editor'))
            l = gtk.Label(_("Split _on:"))
            l.set_use_underline(True)
            l.set_mnemonic_widget(e)
            hb.pack_start(l, expand=False)
            hb.pack_start(e)
            cb = qltk.ConfigCheckButton(
                _("Show _programmatic comments"), 'settings', 'allcomments')
            cb.set_active(config.state("allcomments"))
            vbox.pack_start(hb, expand=False)
            vbox.pack_start(cb, expand=False)
            f.child.add(vbox)
            self.pack_start(f)
            self.show_all()

        def __select(self, button, entry, initial):
            chooser = FileChooser(self.parent.parent.parent,
                                  _("Select Directories"), initial)
            resp, fns = chooser.run()
            chooser.destroy()
            if resp == gtk.RESPONSE_OK:
                entry.set_text(":".join(map(util.fsdecode, fns)))

        def _changed(self, entry, name):
            config.set('settings', name,
                       util.fsencode(entry.get_text().decode('utf-8')))

    class Plugins(_Pane, gtk.VBox):
        def __init__(self):
            gtk.VBox.__init__(self, spacing=6)
            self.set_border_width(12)
            self.title = _("Plugins")

            sw = gtk.ScrolledWindow()
            sw.set_policy(gtk.POLICY_NEVER, gtk.POLICY_ALWAYS)
            tv = HintedTreeView()
            model = gtk.ListStore(object)
            tv.set_model(model)
            tv.set_rules_hint(True)

            render = gtk.CellRendererToggle()
            def cell_data(col, render, model, iter):
                render.set_active(
                    widgets.main.pm.enabled(model[iter][0]))
            render.connect('toggled', self.__toggled, model)
            column = gtk.TreeViewColumn("enabled", render)
            column.set_cell_data_func(render, cell_data)
            tv.append_column(column)

            render = gtk.CellRendererPixbuf()
            def cell_data(col, render, model, iter):
                render.set_property(
                    'stock-id', getattr(model[iter][0], 'PLUGIN_ICON',
                                        gtk.STOCK_EXECUTE))
            column = gtk.TreeViewColumn("image", render)
            column.set_cell_data_func(render, cell_data)
            tv.append_column(column)

            render = gtk.CellRendererText()
            render.set_property('ellipsize', pango.ELLIPSIZE_END)
            render.set_property('xalign', 0.0)
            column = gtk.TreeViewColumn("name", render)
            def cell_data(col, render, model, iter):
                render.set_property('text', model[iter][0].PLUGIN_NAME)
            column.set_cell_data_func(render, cell_data)
            column.set_expand(True)
            tv.append_column(column)

            render = gtk.CellRendererText()
            render.set_property('xalign', 1.0)
            column = gtk.TreeViewColumn("version", render)
            def cell_data(col, render, model, iter):
                render.set_property(
                    'text', getattr(model[iter][0], 'PLUGIN_VERSION', ''))
            column.set_cell_data_func(render, cell_data)
            tv.append_column(column)

            sw.add(tv)
            sw.set_shadow_type(gtk.SHADOW_IN)
            self.pack_start(sw, expand=True)

            tv.set_headers_visible(False)
            
            selection = tv.get_selection()
            desc = gtk.Frame()
            lab = gtk.Label()
            lab.set_markup("<b>%s</b>" % tag("description"))
            desc.set_label_widget(lab)
            selection.connect('changed', self.__description, desc)
            self.pack_start(desc, expand=False)

            prefs = gtk.Frame()
            lab = gtk.Label()
            lab.set_markup("<b>%s</b>" % _("Preferences"))
            prefs.set_label_widget(lab)
            selection.connect('changed', self.__preferences, prefs)
            self.pack_start(prefs, expand=False)

            bbox = gtk.HButtonBox()
            bbox.set_spacing(6)
            bbox.set_layout(gtk.BUTTONBOX_END)
            refresh = gtk.Button(stock=gtk.STOCK_REFRESH)
            refresh.connect('clicked', self.__refresh, tv, desc)
            refresh.set_focus_on_click(False)
            bbox.pack_start(refresh)
            self.pack_start(bbox, expand=False)
            self.show_all()
            tv.get_selection().emit('changed')
            refresh.clicked()

        def __description(self, selection, frame):
            model, iter = selection.get_selected()
            if frame.child: frame.child.destroy()
            try: description = model[iter][0].PLUGIN_DESC
            except (TypeError, AttributeError): frame.hide()
            else:
                description = gtk.Label(description)
                description.set_alignment(0, 0)
                description.set_padding(6, 6)
                description.set_line_wrap(True)
                frame.add(description)
                frame.show_all()

        def __preferences(self, selection, frame):
            model, iter = selection.get_selected()
            if frame.child: frame.child.destroy()
            if iter and hasattr(model[iter][0], 'PluginPreferences'):
                try:
                    prefs = model[iter][0].PluginPreferences(
                        self.parent.parent.parent)
                except:
                    import traceback; traceback.print_exc()
                    frame.hide()
                else:
                    if isinstance(prefs, gtk.Window):
                        b = gtk.Button(stock=gtk.STOCK_PREFERENCES)
                        b.connect_object('clicked', gtk.Window.show, prefs)
                        b.connect_object('destroy', gtk.Window.destroy, prefs)
                        frame.add(b)
                    else:
                        frame.add(prefs)
                    frame.show_all()
            else: frame.hide()

        def __toggled(self, render, path, model):
            render.set_active(not render.get_active())
            widgets.main.pm.enable(model[path][0], render.get_active())
            widgets.main.pm.save()
            model[path][0] = model[path][0]

        def __refresh(self, activator, view, desc):
            model, sel = view.get_selection().get_selected()
            if sel: sel = model[sel][0]
            model.clear()
            widgets.main.pm.rescan()
            plugins = widgets.main.pm.list()
            plugins.sort(lambda a, b: cmp(a.PLUGIN_NAME, b.PLUGIN_NAME))
            for plugin in plugins:
                it = model.append(row=[plugin])
                if plugin is sel: view.get_selection().select_iter(it)
            if not plugins:
                if desc.child: desc.child.destroy()
                desc.add(gtk.Label(_("No plugins found.")))
                desc.child.set_padding(6, 6)

    def __init__(self, parent):
        gtk.Window.__init__(self)
        self.set_title(_("Quod Libet Preferences"))
        self.set_border_width(12)
        self.set_resizable(False)
        self.set_transient_for(parent)
        icon_theme = gtk.icon_theme_get_default()
        self.set_icon(icon_theme.load_icon(
            const.ICON, 64, gtk.ICON_LOOKUP_USE_BUILTIN))
        self.add(gtk.VBox(spacing=12))
        tips = gtk.Tooltips()
        n = qltk.Notebook()
        self.child.pack_start(n)

        bbox = gtk.HButtonBox()
        bbox.set_layout(gtk.BUTTONBOX_END)
        button = gtk.Button(stock=gtk.STOCK_CLOSE)
        button.connect_object('clicked', gtk.Window.destroy, self)
        bbox.pack_start(button)
        self.connect_object('destroy', PreferencesWindow.__destroy, self)
        self.child.pack_start(bbox, expand=False)
        self.child.show_all()

        for Page in [self.SongList, self.Browsers, self.Player,
                     self.Library, self.Plugins]:
            n.append_page(Page())

    def __destroy(self):
        del(widgets.preferences)
        config.write(const.CONFIG)

class DeleteDialog(gtk.Dialog):
    def __init__(self, parent, files):
        gtk.Dialog.__init__(self, _("Deleting files"), parent)
        self.set_border_width(6)
        self.vbox.set_spacing(6)
        self.action_area.set_border_width(0)
        self.set_resizable(False)
        # This is the GNOME trash can for at least some versions.
        # The FreeDesktop spec is complicated and I'm not sure it's
        # actually used by anything.
        if os.path.isdir(os.path.expanduser("~/.Trash")):
            b = qltk.Button(_("_Move to Trash"), gtk.STOCK_DELETE)
            self.add_action_widget(b, 0)

        self.add_button(gtk.STOCK_CANCEL, 1)
        self.add_button(gtk.STOCK_DELETE, 2)

        hbox = gtk.HBox()
        hbox.set_border_width(6)
        i = gtk.Image()
        i.set_from_stock(gtk.STOCK_DIALOG_WARNING, gtk.ICON_SIZE_DIALOG)
        i.set_padding(12, 0)
        i.set_alignment(0.5, 0.0)
        hbox.pack_start(i, expand=False)
        vbox = gtk.VBox(spacing=6)
        base = os.path.basename(files[0])
        if len(files) == 1:
            l = _("Permanently delete this file?")
            exp = gtk.Expander("%s" % util.fsdecode(base))
        else:
            l = _("Permanently delete these files?")
            exp = gtk.Expander(_("%s and %d more...") %(
                util.fsdecode(base), len(files) - 1))

        lab = gtk.Label()
        lab.set_markup("<big><b>%s</b></big>" % l)
        lab.set_alignment(0.0, 0.5)
        vbox.pack_start(lab, expand=False)

        lab = gtk.Label("\n".join(
            map(util.fsdecode, map(util.unexpand, files))))
        lab.set_alignment(0.1, 0.0)
        exp.add(gtk.ScrolledWindow())
        exp.child.add_with_viewport(lab)
        exp.child.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        exp.child.child.set_shadow_type(gtk.SHADOW_NONE)
        vbox.pack_start(exp)
        hbox.pack_start(vbox)
        self.vbox.pack_start(hbox)
        self.vbox.show_all()

class BigCenteredImage(gtk.Window):
    def __init__(self, title, filename):
        gtk.Window.__init__(self)
        width = gtk.gdk.screen_width() / 2
        height = gtk.gdk.screen_height() / 2
        pixbuf = gtk.gdk.pixbuf_new_from_file(filename)

        x_rat = pixbuf.get_width() / float(width)
        y_rat = pixbuf.get_height() / float(height)
        if x_rat > 1 or y_rat > 1:
            if x_rat > y_rat: height = int(pixbuf.get_height() / x_rat)
            else: width = int(pixbuf.get_width() / y_rat)
            pixbuf = pixbuf.scale_simple(
                width, height, gtk.gdk.INTERP_BILINEAR)

        self.set_title(title)
        self.set_decorated(False)
        self.set_position(gtk.WIN_POS_CENTER)
        self.set_modal(False)
        self.set_icon(pixbuf)
        self.add(gtk.Frame())
        self.child.set_shadow_type(gtk.SHADOW_OUT)
        self.child.add(gtk.EventBox())
        self.child.child.add(gtk.Image())
        self.child.child.child.set_from_pixbuf(pixbuf)

        # The eventbox
        self.child.child.connect_object(
            'button-press-event', BigCenteredImage.__destroy, self)
        self.child.child.connect_object(
            'key-press-event', BigCenteredImage.__destroy, self)
        self.show_all()

    def __destroy(self, event):
        self.destroy()

class TrayIcon(object):
    def __init__(self, pixbuf, cbs):
        try:
            import trayicon
        except:
            self.__icon = None
        else:
            self.__icon = trayicon.TrayIcon('quodlibet')
            self.__tips = gtk.Tooltips()
            eb = gtk.EventBox()
            i = gtk.Image()
            i.set_from_pixbuf(pixbuf)
            eb.add(i)
            self.__mapped = False
            self.__icon.connect('map-event', self.__got_mapped, True)
            self.__icon.connect('unmap-event', self.__got_mapped, False)
            self.__icon.add(eb)
            self.__icon.child.connect("button-press-event", self.__event)
            self.__icon.child.connect("scroll-event", self.__scroll)
            self.__cbs = cbs
            self.__icon.show_all()
            print to(_("Initialized status icon."))

    def __got_mapped(self, s, event, value):
        self.__mapped = value

    def __enabled(self):
        return ((self.__icon is not None) and
                (self.__mapped) and
                (self.__icon.get_property('visible')))

    enabled = property(__enabled)

    def __event(self, widget, event, button=None):
        c = self.__cbs.get(button or event.button)
        if callable(c): c(event)

    def __scroll(self, widget, event):
        button = {gtk.gdk.SCROLL_DOWN: 4,
                  gtk.gdk.SCROLL_UP: 5,
                  gtk.gdk.SCROLL_RIGHT: 6,
                  gtk.gdk.SCROLL_LEFT: 7}.get(event.direction)
        self.__event(widget, event, button)


    def __set_tooltip(self, tooltip):
        if self.__icon: self.__tips.set_tip(self.__icon, tooltip)

    tooltip = property(None, __set_tooltip)

    def destroy(self):
        if self.__icon: self.__icon.destroy()

class PlaylistWindow(gtk.Window):

    # If we open a playlist whose window is already displayed, bring it
    # to the forefront rather than make a new one.
    __list_windows = {}
    def __new__(klass, name, *args, **kwargs):
        win = klass.__list_windows.get(name, None)
        if win is None:
            win = super(PlaylistWindow, klass).__new__(
                klass, name, *args, **kwargs)
            win.__initialize_window(name)
            klass.__list_windows[name] = win
            # insert sorted, unless present
            def insert_sorted(model, path, iter, last_try):
                if model[iter][1] == win.__plname:
                    return True # already present
                if model[iter][1] > win.__plname:
                    model.insert_before(iter, [win.__prettyname, win.__plname])
                    return True # inserted
                if path[0] == last_try:
                    model.insert_after(iter, [win.__prettyname, win.__plname])
                    return True # appended
            model = PlayList.lists_model()
            model.foreach(insert_sorted, len(model) - 1)
        return win

    def __init__(self, name):
        self.present()

    def set_name(self, name):
        self.__prettyname = name
        self.__plname = PlayList.normalize_name(name)
        self.set_title('Quod Libet Playlist: %s' % name)

    def __destroy(self, view):
        del(self.__list_windows[self.__prettyname])
        if not len(view.get_model()):
            def remove_matching(model, path, iter, name):
                if model[iter][1] == name:
                    model.remove(iter)
                    return True
            PlayList.lists_model().foreach(remove_matching, self.__plname)

    def __initialize_window(self, name):
        gtk.Window.__init__(self)
        icon_theme = gtk.icon_theme_get_default()
        self.set_icon(icon_theme.load_icon(
            const.ICON, 64, gtk.ICON_LOOKUP_USE_BUILTIN))
        self.set_destroy_with_parent(True)
        self.set_default_size(400, 400)
        self.set_border_width(12)

        vbox = gtk.VBox(spacing=6)
        self.add(vbox)

        self.__view = view = PlayList(name)
        bar = SearchBar(self.__add_query_results, gtk.STOCK_ADD, save=False)
        vbox.pack_start(bar, expand=False, fill=False)

        hbox = gtk.HButtonBox()
        hbox.set_layout(gtk.BUTTONBOX_END)
        vbox.pack_end(hbox, expand=False)
        vbox.pack_end(gtk.HSeparator(), expand=False)

        close = gtk.Button(stock=gtk.STOCK_CLOSE)
        hbox.pack_end(close, expand=False)

        swin = gtk.ScrolledWindow()
        swin.set_shadow_type(gtk.SHADOW_IN)
        swin.set_policy(gtk.POLICY_NEVER, gtk.POLICY_AUTOMATIC)
        vbox.pack_start(swin)

        swin.add(view)

        self.set_name(name)
        self.connect_object('destroy', self.__destroy, view)
        close.connect_object('clicked', gtk.Window.destroy, self)
        self.show_all()

    def __add_query_results(self, text, sort):
        query = text.decode('utf-8').strip()
        try:
           songs = library.query(query)
           songs.sort()
           self.__view.append_songs(songs)
        except ValueError: pass

# A tray icon aware of UI policy -- left click shows/hides, right
# click makes a callback.
class HIGTrayIcon(TrayIcon):
    def __init__(self, pixbuf, window, cbs={}):
        self.__window = window
        cbs[1] = self.__showhide
        TrayIcon.__init__(self, pixbuf, cbs)

    def hide_window(self):
        if self.__window.get_property('visible'):
            self.__showhide(None)

    def __showhide(self, event):
        if self.__window.get_property('visible'):
            self.__pos = self.__window.get_position()
            self.__window.hide()
        else:
            self.__window.move(*self.__pos)
            self.__window.show()

class QLTrayIcon(HIGTrayIcon):
    def __init__(self, window, volume):
        menu = gtk.Menu()
        playpause = gtk.ImageMenuItem(gtk.STOCK_MEDIA_PLAY)
        playpause.connect('activate', self.__playpause)

        previous = gtk.ImageMenuItem(gtk.STOCK_MEDIA_PREVIOUS)
        previous.connect('activate', lambda *args: player.playlist.previous())
        next = gtk.ImageMenuItem(gtk.STOCK_MEDIA_NEXT)
        next.connect('activate', lambda *args: player.playlist.next())

        props = gtk.ImageMenuItem(gtk.STOCK_PROPERTIES)
        props.connect('activate', self.__properties)

        quit = gtk.ImageMenuItem(gtk.STOCK_QUIT)
        quit.connect('activate', gtk.main_quit)

        for item in [playpause, gtk.SeparatorMenuItem(), previous, next,
                     gtk.SeparatorMenuItem(), props, gtk.SeparatorMenuItem(),
                     quit]: menu.append(item)

        menu.show_all()

        widgets.watcher.connect('song-started', self.__set_song, next, props)
        widgets.watcher.connect('paused', self.__set_paused, menu, True)
        widgets.watcher.connect('unpaused', self.__set_paused, menu, False)

        cbs = {
            2: lambda *args: self.__playpause(args[0]),
            3: lambda ev, *args:
            menu.popup(None, None, None, ev.button, ev.time),
            4: lambda *args: volume.set_value(volume.get_value()-0.05),
            5: lambda *args: volume.set_value(volume.get_value()+0.05),
            6: lambda *args: player.playlist.next(),
            7: lambda *args: player.playlist.previous()
            }

        icon_theme = gtk.icon_theme_get_default()
        p = icon_theme.load_icon(
            const.ICON, 16, gtk.ICON_LOOKUP_USE_BUILTIN)

        HIGTrayIcon.__init__(self, p, window, cbs)

    def __set_paused(self, watcher, menu, paused):
        menu.get_children()[0].destroy()
        stock = [gtk.STOCK_MEDIA_PAUSE, gtk.STOCK_MEDIA_PLAY][paused]
        playpause = gtk.ImageMenuItem(stock)
        playpause.connect('activate', self.__playpause)
        playpause.show()
        menu.prepend(playpause)

    def __playpause(self, activator):
        if widgets.watcher.song: player.playlist.paused ^= True
        else: player.playlist.reset()

    def __properties(self, activator):
        if widgets.watcher.song: SongProperties([widgets.watcher.song])

    def __set_song(self, watcher, song, *items):
        for item in items: item.set_sensitive(bool(song))
        if song:
            try:
                pattern = util.FileFromPattern(
                    config.get("plugins", "icon_tooltip"), filename=False)
            except ValueError:
                pattern = util.FileFromPattern(
                    "<album|<album~discnumber~part~tracknumber~title~version>|"
                    "<artist~title~version>>", filename=False)
            self.tooltip = pattern.match(song)
        else: self.tooltip = _("Not playing")

class MmKeys(object):
    def __init__(self, cbs):
        try:
            import mmkeys
        except: pass
        else:
            self.__keys = mmkeys.MmKeys()
            map(self.__keys.connect, *zip(*cbs.items()))
            print to(_("Initialized multimedia key support."))

class Osd(object):
    def __init__(self, watcher):
        try: import gosd
        except: pass
        else:
            self.__gosd = gosd
            self.__level = 0
            self.__window = None
            watcher.connect('song-started', self.__show_osd)

    def __show_osd(self, watcher, song):
        if song is None or config.getint("settings", "osd") == 0: return
        color1, color2 = config.get("settings", "osdcolors").split()
        font = config.get("settings", "osdfont")

        if self.__window: self.__window.destroy()

        # \xe2\x99\xaa is a music note.
        msg = "\xe2\x99\xaa "

        msg += "<span foreground='%s' style='italic'>%s</span>" %(
            color2, util.escape(song("~title~version")))
        msg += " <span size='small'>(%s)</span> " % song("~length")
        msg += "\xe2\x99\xaa\n"

        msg += "<span size='x-small'>"
        for key in ["artist", "album", "tracknumber"]:
            if key in song:
                msg += ("<span foreground='%s' size='xx-small' "
                        "style='italic'>%s</span> %s   "%(
                    (color2, tag(key), util.escape(song.comma(key)))))
        msg = msg.strip() + "</span>"
        if isinstance(msg, unicode):
            msg = msg.encode("utf-8")

        self.__window = self.__gosd.osd(msg, "black", color1, font)
        if config.getint("settings", "osd") == 1:
            self.__window.move(
                gtk.gdk.screen_width()/2 - self.__window.width/2, 5)
        else:
            self.__window.move(
                gtk.gdk.screen_width()/2 - self.__window.width/2,
                gtk.gdk.screen_height() - self.__window.height-48)
        self.__window.show()
        self.__level += 1
        gobject.timeout_add(7500, self.__unshow)

    def __unshow(self):
        self.__level -= 1
        if self.__level == 0 and self.__window:
            self.__window.destroy()
            self.__window = None

class Browser(object):
    expand = False # Packing options
    background = True # Use browsers/filter as a background filter

    # read config data
    def restore(self): pass

    # decides whether "filter on foo" menu entries are available
    def can_filter(self, key): return False

class PanedBrowser(Browser, gtk.VBox):
    expand = gtk.VPaned
    
    class Pane(gtk.ScrolledWindow):
        def __init__(self, mytag, next, play=True):
            gtk.ScrolledWindow.__init__(self)
            self.set_policy(gtk.POLICY_NEVER, gtk.POLICY_AUTOMATIC)
            self.set_shadow_type(gtk.SHADOW_IN)
            self.add(HintedTreeView(gtk.ListStore(str)))
            render = gtk.CellRendererText()
            render.set_property('ellipsize', pango.ELLIPSIZE_END)
            column = gtk.TreeViewColumn(tag(mytag), render, markup=0)
            column.set_sizing(gtk.TREE_VIEW_COLUMN_FIXED)
            column.set_fixed_width(50)
            self.child.append_column(column)
            self.tag = mytag
            self.__next = next
            self.__songs = []
            self.__selected_items = []
            self.child.get_selection().set_mode(gtk.SELECTION_MULTIPLE)
            self.child.connect_object('destroy', self.child.set_model, None)
            self.__sig = self.child.get_selection().connect(
                'changed', self.__selection_changed)
            if play:
                self.child.connect('row-activated', self.__play_selection)

        def __play_selection(self, view, indices, col):
            player.playlist.next()
            player.playlist.reset()

        def __selection_changed(self, selection, check=True, jump=False):
            if check: # verify we've actually changed...
                model, rows = selection.get_selected_rows()
                selected_items = [model[row][0] for row in rows]
                if rows == []: rows = [(0,)]
                if self.__selected_items == selected_items: return
                else: self.__selected_items = selected_items
            else:
                model, rows = selection.get_selected_rows()
                self.__selected_items = [model[row][0] for row in rows]
            if jump:
                model, rows = selection.get_selected_rows()
                if rows: self.child.scroll_to_cell(rows[0])
            # pass on the remaining songs to the next pane
            self.__next.fill(
                filter(parser.parse(self.query()).search, self.__songs))

        def select(self, values, escape=True):
            selection = self.child.get_selection()
            selection.handler_block(self.__sig)
            selection.unselect_all()
            model = selection.get_tree_view().get_model()
            if values == []: selection.select_path((len(model) - 1,))
            elif values is None: selection.select_path((0,))
            else:
                if escape:
                    values = [util.escape(v.encode('utf-8')) for v in values]
                def select_values(model, path, iter):
                    if model[path][0] in values:
                        selection.select_path(path)
                model.foreach(select_values)
            selection.handler_unblock(self.__sig)
            self.__selection_changed(selection, check=False, jump=True)

        def fill(self, songs, handle_pending=True):
            self.__songs = songs
            # get values from song list
            complete = True
            values = set()
            for song in songs:
                l = song.list(self.tag)
                values.update(l)
                complete = complete and bool(l)
            values = list(values); values.sort()

            # record old selection data to preserve as much as possible
            selection = self.child.get_selection()
            selection.handler_block(self.__sig)
            model, rows = selection.get_selected_rows()
            selected_items = [model[row][0] for row in rows]
            # fill in the new model
            model = self.child.get_model()
            model.clear()
            to_select = []
            if len(values) + (not bool(complete)) > 1:
                model.append(["<b>%s</b>" % _("All")])
            for i, value in enumerate(map(util.escape, values)):
                model.append([value])
                if value in selected_items: to_select.append(i + 1)
            if not complete:
                model.append(["<b>%s</b>" % _("Unknown")])
            if to_select == []: to_select = [0]
            for i in to_select: selection.select_path((i,))
            selection.handler_unblock(self.__sig)
            self.__selection_changed(selection, check=False, jump=True)

        def query(self):
            selection = self.child.get_selection()
            model, rows = selection.get_selected_rows()
            if rows == [] or rows[0][0] == 0: # All
                return "%s = /.?/" % self.tag
            else:
                selected = ["/^%s$/c"%sre.escape(util.unescape(model[row][0]))
                            for row in rows]
                if model[rows[-1]][0].startswith("<b>"): # Not All, so Unknown
                    selected.pop()
                    selected.append("!/./")
                return ("%s = |(%s)" %(
                    self.tag, ", ".join(selected))).decode("utf-8")

    def __init__(self, cb, save=True, play=True):
        gtk.VBox.__init__(self, spacing=0)
        self.__cb = cb
        self.__save = save
        self.__play = play

        self.refresh_panes(restore=False)

        s = widgets.watcher.connect('refresh', self.__refresh)
        self.connect_object('destroy', widgets.watcher.disconnect, s)

    def refresh_panes(self, restore=True):
        try: hbox = self.get_children()[1]
        except IndexError: pass # first call
        else: hbox.destroy()

        hbox = gtk.HBox(spacing=3)
        hbox.set_homogeneous(True)
        hbox.set_size_request(100, 100)
        # fill in the pane list. the last pane reports back to us.
        self.__panes = [self]
        panes = config.get("browsers", "panes").split(); panes.reverse()
        for pane in panes:
            self.__panes.insert(
                0, self.Pane(pane, self.__panes[0], self.__play))
        self.__panes.pop() # remove self
        map(hbox.pack_start, self.__panes)
        self.pack_start(hbox)
        self.__inhibit = True
        self.__panes[0].fill(library.values())
        if restore: self.restore()
        self.show_all()

    def can_filter(self, key):
        return key in [pane.tag for pane in self.__panes]

    def filter(self, key, values):
        for pane in self.__panes:
            self.__inhibit = True
            pane.select(None)

        pane = self.__panes[[pane.tag for pane in self.__panes].index(key)]
        pane.select(values)

    def save(self):
        selected = []
        for pane in self.__panes:
            selection = pane.child.get_selection()
            model, rows = selection.get_selected_rows()
            selected.append("\t".join([model[row][0] for row in rows]))
        config.set("browsers", "pane_selection", "\n".join(selected))

    def restore(self):
        try:
            selections = [y.split("\t") for y in
                          config.get("browsers", "pane_selection").split("\n")]
        except: pass
        else:
            if len(selections) == len(self.__panes):
                for sel, pane in zip(selections, self.__panes):
                    self.__inhibit = True
                    pane.select(sel, escape=False)

    def activate(self):
        self.fill(None)

    def __refresh(self, watcher):
        self.__inhibit = True
        self.__panes[0].fill(library.values())

    def fill(self, songs):
        if self.__inhibit: self.__inhibit = False
        else:
            if self.__save: self.save()
            self.__cb(
                "&(%s)" % ", ".join(map(self.Pane.query, self.__panes)), None)

class PlaylistBar(Browser, gtk.HBox):
    background = False

    def __init__(self, cb):
        gtk.HBox.__init__(self)
        combo = gtk.ComboBox(PlayList.lists_model())
        cell = gtk.CellRendererText()
        combo.pack_start(cell, True)
        combo.add_attribute(cell, 'text', 0)
        combo.set_active(0)
        self.pack_start(combo)

        edit = gtk.Button()
        refresh = gtk.Button()
        edit.add(gtk.image_new_from_stock(gtk.STOCK_EDIT, gtk.ICON_SIZE_MENU))
        refresh.add(gtk.image_new_from_stock(gtk.STOCK_REFRESH,
                                             gtk.ICON_SIZE_MENU))
        edit.set_sensitive(False)
        refresh.set_sensitive(False)
        self.pack_start(edit, expand=False)
        self.pack_start(refresh, expand=False)
        edit.connect('clicked', self.__edit_current, combo)
        combo.connect('changed', self.__list_selected, edit, refresh)
        refresh.connect_object(
            'clicked', self.__list_selected, combo, edit, refresh)

        self.__cb = cb
        tips = gtk.Tooltips()
        tips.set_tip(edit, _("Edit the current playlist"))
        tips.set_tip(refresh, _("Refresh the current playlist"))
        self.show_all()
        self.connect_object('destroy', combo.set_model, None)
        self.connect_object('destroy', gtk.Tooltips.destroy, tips)
        tips.enable()

    def save(self):
        combo = self.get_children()[0]
        active = combo.get_active()
        key = combo.get_model()[active][1]
        config.set("browsers", "playlist", key)

    def restore(self):
        try: key = config.get("browsers", "playlist")
        except Exception: pass
        else:
            combo = self.get_children()[0]
            model = combo.get_model()
            def find_key(model, path, iter, key):
                if model[iter][1] == key:
                    combo.set_active(path[0])
                    return True
            model.foreach(find_key, key)

    def activate(self):
        self.__list_selected(*self.get_children())

    def __list_selected(self, combo, edit, refresh):
        active = combo.get_active()
        edit.set_sensitive(active != 0)
        refresh.set_sensitive(active != 0)
        self.save()
        if active == 0:
            self.__cb("", None)
        else:
            playlist = "playlist_" + combo.get_model()[active][1]
            self.__cb("#(%s > 0)" % playlist, "~#"+playlist)

    def __edit_current(self, edit, combo):
        active = combo.get_active()
        if active > 0: PlaylistWindow(combo.get_model()[active][0])

class CoverImage(gtk.Frame):
    def __init__(self, size=None):
        gtk.Frame.__init__(self)
        self.add(gtk.EventBox())
        self.child.add(gtk.Image())
        self.__size = size or [120, 100]
        self.child.child.set_size_request(-1, self.__size[1])
        self.child.connect_object(
            'button-press-event', CoverImage.__show_cover, self)
        self.child.show_all()
        self.__albumfn = None

    def set_song(self, activator, song):
        self.__song = song
        if song is None:
            self.child.child.set_from_pixbuf(None)
            self.__albumfn = None
            self.hide()
        else:
            cover = song.find_cover()
            if cover is None:
                self.__albumfn = None
                self.child.child.set_from_pixbuf(None)
                self.hide()
            elif cover.name != self.__albumfn:
                try:
                    pixbuf = gtk.gdk.pixbuf_new_from_file_at_size(
                        cover.name, *self.__size)
                except gobject.GError:
                    self.hide()
                else:
                    self.child.child.set_from_pixbuf(pixbuf)
                    self.__albumfn = cover.name
                    self.show()

    def show(self):
        if config.state("cover") and self.__albumfn:
            gtk.Frame.show(self)

    def __show_cover(self, event):
        if (self.__song and event.button == 1 and
            event.type == gtk.gdk._2BUTTON_PRESS):
            cover = self.__song.find_cover()
            BigCenteredImage(self.__song.comma("album"), cover.name)

class HintedTreeView(gtk.TreeView):
    def __init__(self, *args):
        gtk.TreeView.__init__(self, *args)
        try: tvh = widgets.treeviewhints
        except AttributeError: tvh = widgets.treeviewhints = TreeViewHints()
        tvh.connect_view(self)

class TreeViewHints(gtk.Window):
    """Handle 'hints' for treeviews. This includes expansions of truncated
    columns, and in the future, tooltips."""

    __gsignals__ = dict.fromkeys(
            ['button-press-event', 'button-release-event',
            'motion-notify-event', 'key-press-event', 'key-release-event'],
            'override')

    def __init__(self):
        gtk.Window.__init__(self, gtk.WINDOW_POPUP)
        self.__label = label = gtk.Label()
        label.set_alignment(0.5, 0.5)
        self.realize()
        self.add_events(gtk.gdk.BUTTON_MOTION_MASK | gtk.gdk.BUTTON_PRESS_MASK |
                gtk.gdk.BUTTON_RELEASE_MASK | gtk.gdk.KEY_PRESS_MASK |
                gtk.gdk.KEY_RELEASE_MASK | gtk.gdk.ENTER_NOTIFY_MASK |
                gtk.gdk.LEAVE_NOTIFY_MASK | gtk.gdk.SCROLL_MASK)
        self.add(label)

        self.set_app_paintable(True)
        self.set_resizable(False)
        self.set_name("gtk-tooltips")
        self.set_border_width(1)
        self.connect('expose-event', self.__expose)
        self.connect('enter-notify-event', self.__enter)
        self.connect('leave-notify-event', self.__check_undisplay)
        self.connect('scroll-event', self.__scroll)

        self.__handlers = {}
        self.__current_path = self.__current_col = None
        self.__current_renderer = None

    def connect_view(self, view):
        self.__handlers[view] = [
            view.connect('motion-notify-event', self.__motion),
            view.connect('scroll-event', self.__undisplay),
            view.connect('destroy', self.disconnect_view),
        ]

    def disconnect_view(self, view):
        try:
            for handler in self.__handlers[view]: view.disconnect(handler)
            del self.__handlers[view]
        except KeyError: pass

    def __expose(self, widget, event):
        w, h = self.get_size_request()
        self.style.paint_flat_box(self.window,
                gtk.STATE_NORMAL, gtk.SHADOW_OUT,
                None, self, "tooltip", 0, 0, w, h)

    def __enter(self, widget, event):
        # on entry, kill the hiding timeout
        try: gobject.source_remove(self.__timeout_id)
        except AttributeError: pass
        else: del self.__timeout_id

    def __motion(self, view, event):
        # trigger over row area, not column headers
        if event.window is not view.get_bin_window(): return

        x, y = map(int, [event.x, event.y])
        try: path, col, cellx, celly = view.get_path_at_pos(x, y)
        except TypeError: return # no hints where no rows exist

        if self.__current_path == path and self.__current_col == col: return

        # need to handle more renderers later...
        try: renderer, = col.get_cell_renderers()
        except ValueError: return
        if not isinstance(renderer, gtk.CellRendererText): return
        if renderer.get_property('ellipsize') == pango.ELLIPSIZE_NONE: return

        model = view.get_model()
        col.cell_set_cell_data(model, model.get_iter(path), False, False)
        cellw = col.cell_get_position(renderer)[1]

        label = self.__label
        rect = gtk.gdk.Rectangle(0, 0, 4, 4) # small rect, use to get text
        renderer.render(self.window, self, rect, rect, rect, 0)
        label.set_text(renderer.get_property('text'))
        w, h0 = label.get_layout().get_pixel_size()
        try: markup = renderer.markup
        except AttributeError: h1 = h0
        else:
            if isinstance(markup, int): markup = model[path][markup]
            self.__label.set_markup(markup)
            w, h1 = label.get_layout().get_pixel_size()

        if w + 5 < cellw: return # don't display if it doesn't need expansion

        x, y, cw, h = list(view.get_cell_area(path, col))
        self.__dx = x + 1
        self.__dy = y + 1
        y += view.get_bin_window().get_position()[1]
        ox, oy = view.window.get_origin()
        x += ox; y += oy; h += h1 - h0; w += 5
        screen_width = gtk.gdk.screen_width()
        x_overflow = min([x, x + w - screen_width])
        label.set_ellipsize(pango.ELLIPSIZE_NONE)
        if x_overflow > 0:
            self.__dx -= x_overflow
            x -= x_overflow
            w = min([w, screen_width])
            label.set_ellipsize(pango.ELLIPSIZE_END)
        if not((x<=int(event.x_root) < x+w) and (y <= int(event.y_root) < y+h)):
            return # reject if cursor isn't above hint

        self.__target = view
        self.__current_renderer = renderer
        self.__edit_id = renderer.connect('editing-started', self.__undisplay)
        self.__current_path = path
        self.__current_col = col
        self.__time = event.time
        self.__timeout(id=gobject.timeout_add(100, self.__undisplay))
        self.set_size_request(w, h)
        self.move(x, y)
        self.show_all()

    def __check_undisplay(self, ev1, event):
        if self.__time < event.time + 50: self.__undisplay()

    def __undisplay(self, *args):
        if self.__current_renderer and self.__edit_id:
            self.__current_renderer.disconnect(self.__edit_id)
        self.__current_renderer = self.__edit_id = None
        self.__current_path = self.__current_col = None
        self.hide()

    def __timeout(self, ev=None, event=None, id=None):
        try: gobject.source_remove(self.__timeout_id)
        except AttributeError: pass
        if id is not None: self.__timeout_id = id

    def do_button_press_event(self, event):
        event.x += self.__dx
        event.y += self.__dy 
        event.window = self.__target.get_bin_window()
        return self.__target.do_button_press_event(self.__target, event)

    def do_button_release_event(self, event):
        event.x += self.__dx
        event.y += self.__dy 
        event.window = self.__target.get_bin_window()
        return self.__target.do_button_release_event(self.__target, event)

    def do_motion_notify_event(self, event):
        event.x += self.__dx
        event.y += self.__dy 
        event.window = self.__target.get_bin_window()
        return self.__target.do_motion_notify_event(self.__target, event)

    def __scroll(self, widget, event):
        event.window = self.__target.get_bin_window()
        event.put()

    def do_key_press_event(self, event):
        return self.__target.do_key_press_event(self.__target, event)

    def do_key_release_event(self, event):
        return self.__target.do_key_release_event(self.__target, event)

gobject.type_register(TreeViewHints)

class EmptyBar(Browser, gtk.HBox):
    def __init__(self, cb):
        gtk.HBox.__init__(self)
        self._text = ""
        self._cb = cb

    def set_text(self, text):
        self._text = text

    def save(self):
        config.set("browsers", "query_text", self._text)

    def restore(self):
        try: self.set_text(config.get("browsers", "query_text"))
        except Exception: pass

    def activate(self):
        self._cb(self._text, None)
        self.save()

    def can_filter(self, key):
        return True

    def filter(self, key, values):
        if key.startswith("~#"):
            nheader = key[2:]
            queries = ["#(%s = %d)" % (nheader, i) for i in values]
            self.set_text("|(" + ", ".join(queries) + ")")
        else:
            text = ", ".join(
                ["'%s'c" % v.replace("\\", "\\\\").replace("'", "\\'")
                 for v in values])
            if key.startswith("~"): key = key[1:]
            self.set_text(u"%s = |(%s)" % (key, text))
        self.activate()

class SearchBar(EmptyBar):
    def __init__(self, cb, button=gtk.STOCK_FIND, save=True, play=True):
        EmptyBar.__init__(self, cb)
        self.__save = save

        tips = gtk.Tooltips()
        combo = qltk.ComboBoxEntrySave(
            const.QUERIES, model="searchbar", count=15)
        clear = gtk.Button()
        clear.add(gtk.image_new_from_stock(gtk.STOCK_CLEAR,gtk.ICON_SIZE_MENU))
        tips.set_tip(clear, _("Clear search text"))
        clear.connect('clicked', self.__clear, combo)
                  
        search = gtk.Button()
        b = gtk.HBox(spacing=2)
        b.pack_start(gtk.image_new_from_stock(button, gtk.ICON_SIZE_MENU))
        b.pack_start(gtk.Label(_("Search")))
        search.add(b)
        tips.set_tip(search, _("Search your audio library"))
        search.connect_object('clicked', self.__text_parse, combo.child)
        combo.child.connect('activate', self.__text_parse)
        combo.child.connect('changed', self.__test_filter)
        tips.enable()
        self.connect_object('destroy', gtk.Tooltips.destroy, tips)
        self.pack_start(combo)
        self.pack_start(clear, expand=False)
        self.pack_start(search, expand=False)
        self.show_all()

    def __clear(self, button, combo):
        combo.child.set_text("")

    def activate(self):
        self.get_children()[-1].clicked()

    def set_text(self, text):
        self.get_children()[0].child.set_text(text)
        self._text = text

    def __text_parse(self, entry):
        text = entry.get_text()
        if parser.is_parsable(text):
            self._text = text
            self.get_children()[0].prepend_text(text)
            self._cb(text, None)
            if self.__save: self.save()
            self.get_children()[0].write(const.QUERIES)

    def __test_filter(self, textbox):
        if not config.getboolean('browsers', 'color'): return
        text = textbox.get_text()
        gobject.idle_add(
            self.__set_entry_color, textbox, parser.is_valid_color(text))

    # Set the color of some text.
    def __set_entry_color(self, entry, color):
        layout = entry.get_layout()
        text = layout.get_text()
        markup = '<span foreground="%s">%s</span>' %(
            color, util.escape(text))
        layout.set_markup(markup)

class AlbumList(Browser, gtk.VBox):
    expand = gtk.HPaned

    class _Album(object):
        __covers = {}

        def clear_cache(klass): klass.__covers.clear()
        clear_cache = classmethod(clear_cache)

        def __init__(self, title):
            self.length = 0
            self.discs = 1
            self.tracks = 0
            self.people = set()
            self.title = title
            self.date = None
            self.cover_done = False

            if self.title:
                if self.title in self.__covers:
                    self.cover = self.__covers[title]
                else:
                    self.cover = None
            else:
                self.cover_done = True
                self.cover = None

        def __getitem__(self, key):
            if key == "~#length": return self.length
            elif key == "~#tracks": return self.tracks
            elif key == "~#discs": return self.discs
            elif key == "~length": return util.format_time(self.length)
            elif key == "title": return self.title
            elif key == "date": return self.date
            elif key == "people" or key == "artist" or key == "artists":
                return "\n".join(self.people)
            else: return ""

        def get(self, key, default=None):
            return self[key] or default

        __call__ = get

        def add(self, song):
            self.tracks += 1
            if self.title:
                self.date = song.get("date")
                self.discs = max(self.discs, song("~#disc", 0))
                if self.cover is None:
                    self.cover = False
                    gobject.idle_add(
                        self.__get_cover, song, priority=gobject.PRIORITY_LOW)
            self.length += song["~#length"]
            self.people |= set(song.list("artist"))
            self.people |= set(song.list("performer"))
            self.people |= set(song.list("composer"))

        def __get_cover(self, song):
            cover = song.find_cover()
            if cover is None: return
            else:
                try:
                    cover = gtk.gdk.pixbuf_new_from_file_at_size(
                        cover.name, 48, 48)
                except: pass
                else:
                    # add a black outline
                    w, h = cover.get_width(), cover.get_height()
                    newcover = gtk.gdk.Pixbuf(
                        gtk.gdk.COLORSPACE_RGB, True, 8, w + 2, h + 2)
                    newcover.fill(0x000000ff)
                    cover.copy_area(0, 0, w, h, newcover, 1, 1)
                    self.cover = newcover
                    self.__covers[self.title] = newcover

        def to_markup(self):
            text = "<i><b>%s</b></i>" % util.escape(
                self.title or _("Songs not in an album"))
            if self.date: text += " (%s)" % self.date
            text += "\n<small>"
            if self.discs > 1: text += (_("%d discs") % self.discs) + " - "
            if self.tracks == 1: text += _("1 track")
            else: text += _("%d tracks") % self.tracks
            text += " - %s" % util.format_time_long(self.length)
            people = set(self.people)
            people = list(people); people.sort()
            text += "</small>\n" + ", ".join(map(util.escape, people))
            return text

    def __init__(self, cb, save=True, play=True):
        gtk.VBox.__init__(self)

        self.__cb = cb
        self.__save = save

        sw = gtk.ScrolledWindow()
        sw.set_shadow_type(gtk.SHADOW_IN)
        view = HintedTreeView()
        view.set_headers_visible(False)
        view.set_model(gtk.ListStore(object))

        render = gtk.CellRendererPixbuf()
        column = gtk.TreeViewColumn("covers", render)
        column.set_sizing(gtk.TREE_VIEW_COLUMN_GROW_ONLY)
        render.set_property('xpad', 2)
        render.set_property('ypad', 2)
        render.set_property('width', 56)
        render.set_property('height', 56)

        def cell_data_pb(column, cell, model, iter):
            album = model[iter][0]
            if album is None:
                cell.set_property('pixbuf', None)
            elif album.cover:
                cell.set_property('pixbuf', album.cover)
                if not album.cover_done:
                    model.row_changed(model.get_path(iter), iter)
                    album.cover_done = True
            else: cell.set_property('pixbuf', None)
        column.set_cell_data_func(render, cell_data_pb)
        view.append_column(column)

        render = gtk.CellRendererText()
        column = gtk.TreeViewColumn("albums", render)
        render.set_property('ellipsize', pango.ELLIPSIZE_END)
        def cell_data(column, cell, model, iter):
            album = model[iter][0]
            if album is None:
                text = "<b>%s</b>" % _("All albums")
                text += "\n" + _("%d albums") % (len(model) - 1)
                cell.markup = text
            else:
                cell.markup = model[iter][0].to_markup()
            cell.set_property('markup', cell.markup)
        column.set_cell_data_func(render, cell_data)
        view.append_column(column)

        # Uncommenting causing segfaults when the window is destroyed.
        #view.set_search_column(0)
        #def search_cell(model, column, key, iter):
        #    return not model[iter][0].title.startswith(key.decode('utf-8'))
        #view.set_search_equal_func(search_cell)
        # ... Whether or not we include this.
        #self.connect_object('destroy', view.set_enable_search, False)

        view.get_selection().set_mode(gtk.SELECTION_MULTIPLE)
        view.set_rules_hint(True)
        sw.set_policy(gtk.POLICY_NEVER, gtk.POLICY_AUTOMATIC)
        sw.add(view)
        e = qltk.ValidatingEntry(parser.is_valid_color)

        if play: view.connect('row-activated', self.__play_selection)
        view.get_selection().connect('changed', self.__selection_changed)
        s = widgets.watcher.connect(
            'refresh', self.__refresh, view, e)
        self.connect_object('destroy', widgets.watcher.disconnect, s)

        menu = gtk.Menu()
        button = gtk.ImageMenuItem(gtk.STOCK_REFRESH)
        props = gtk.ImageMenuItem(gtk.STOCK_PROPERTIES)
        menu.append(button)
        menu.append(props)
        menu.show_all()
        button.connect('activate', self.__refresh, view, e, True)
        props.connect('activate', self.__properties, view)

        view.connect_object('popup-menu', gtk.Menu.popup, menu,
                            None, None, None, 2, 0)
        view.connect('button-press-event', self.__button_press, menu)

        e.connect('changed', self.__filter_changed, view)
        self.__refill_id = None

        hb = gtk.HBox(spacing=0)
        lab = gtk.Label(_("_Search:"))
        lab.set_padding(6, 0)
        lab.set_mnemonic_widget(e)
        lab.set_use_underline(True)
        hb.pack_start(lab, expand=False)
        hb.pack_start(e)
        self.pack_start(hb, expand=False)
        self.pack_start(sw, expand=True)

        self.__refresh(None, view, e)
        self.__filter_changed(e, view)
        self.show_all()

    def __filter_changed(self, entry, view):
        if parser.is_parsable(entry.get_text().decode('utf-8').strip()):
            if self.__refill_id: gobject.source_remove(self.__refill_id)
            self.__refill_id = gobject.timeout_add(
                300, self.__refresh, entry, view, entry)

    def __properties(self, activator, view):
        model, rows = view.get_selection().get_selected_rows()
        albums = [model[row][0] for row in rows]
        if None in albums: albums.remove(None)
        if albums:
            text = ", ".join(
                ["'%s'c" % a.title.replace("\\", "\\\\").replace("'", "\\'")
                 for a in albums])
            songs = library.query("album = |(%s)" % text)
            if songs:
                songs.sort()
                SongProperties(songs)

    def __button_press(self, view, event, menu):
        x, y = map(int, [event.x, event.y])
        try: path, col, cellx, celly = view.get_path_at_pos(x, y)
        except TypeError: return True
        if event.button == 3:
            sens = bool(view.get_model()[path][0])
            for c in menu.get_children(): c.set_sensitive(sens)
            view.grab_focus()
            selection = view.get_selection()
            if not selection.path_is_selected(path):
                view.set_cursor(path, col, 0)
            menu.popup(None, None, None, event.button, event.time)
            return True

    def __play_selection(self, view, indices, col):
        player.playlist.next()
        player.playlist.reset()

    def filter(self, key, values):
        assert(key == "album")
        view = self.get_children()[1].child
        selection = view.get_selection()
        selection.unselect_all()
        model = view.get_model()
        first = None
        for i, row in enumerate(iter(model)):
            if row[0] and row[0].title in values:
                selection.select_path(i)
                first = first or i
        if first: view.scroll_to_cell(first)

    def activate(self):
        self.get_children()[1].child.get_selection().emit('changed')

    def can_filter(self, key):
        return (key == "album")

    def restore(self):
        albums = config.get("browsers", "albums").split("\n")
        selection = self.get_children()[1].child.get_selection()
        # there's an ambiguity here -- if albums is "" then it could be
        # either all albums or no albums. If it's "" and some other
        # stuff, assume no albums, otherwise all albums.
        selection.unselect_all()
        if albums == [""]: # all songs
            selection.select_path((0,))
        else:
            model = selection.get_tree_view().get_model()
            first = None
            for i, row in enumerate(iter(model)):
                if row[0] and row[0].title in albums:
                    selection.select_path(i)
                    first = first or i

            if first: selection.get_tree_view().scroll_to_cell(first)

    def __selection_changed(self, selection):
        model, rows = selection.get_selected_rows()
        albums = [model[row][0] for row in rows]
        if None in albums:
            self.__cb(u"", None)
            if self.__save: config.set("browsers", "albums", "")
        else:
            names = [a.title for a in albums]
            text = ", ".join(
                ["'%s'c" % album.replace("\\", "\\\\").replace("'", "\\'")
                 for album in names])
            if text:
                confval = "\n".join(names)
                # Since ConfigParser strips a trailing \n...
                if confval and confval[-1] == "\n":
                    confval = "\n" + confval[:-1]
                if self.__save: config.set("browsers", "albums", confval)
                self.__cb(u"album = |(%s)" % text, None)

    def __refresh(self, watcher, view, entry, clear_cache=False):
        selection = view.get_selection()
        model, rows = selection.get_selected_rows()
        selected = [(model[row][0] and model[row][0].title) for row in rows]
        model = view.get_model()
        if clear_cache: self._Album.clear_cache()
        model.clear()
        albums = {}
        bg = entry.get_text().decode('utf-8').strip()
        songs = library.values()
        if bg and parser.is_parsable(bg): filt = parser.parse(bg).search
        else: filt = None

        for song in songs:
            if "album" not in song:
                if "" not in albums: albums[""] = self._Album("")
                albums[""].add(song)
            else:
                for album in song.list('album'):
                    if album not in albums:
                        albums[album] = self._Album(album)
                    albums[album].add(song)

        albums = filter(filt, albums.values())
        albums.sort(lambda a, b: cmp(a.title, b.title))
        if albums and albums[0].title == "":
            albums.append(albums.pop(0))
        if filt is None: model.append(row=[None])
        for album in albums: model.append(row=[album])
        for i, row in enumerate(iter(model)):
             if (row[0] and row[0].title) in selected:
                  selection.select_path(i)

class MainWindow(gtk.Window):
    class StopAfterMenu(gtk.Menu):
        def __init__(self):
            gtk.Menu.__init__(self)
            self.__item = gtk.CheckMenuItem(_("Stop after this song"))
            self.__item.set_active(False)
            self.append(self.__item)
            self.__item.show()

        def __get_active(self): return self.__item.get_active()
        def __set_active(self, v): return self.__item.set_active(v)
        active = property(__get_active, __set_active)

    class SongInfo(gtk.Label):
        def __init__(self):
            gtk.Label.__init__(self)
            self.set_ellipsize(pango.ELLIPSIZE_END)
            self.set_alignment(0.0, 0.0)
            self.set_padding(3, 3)
            widgets.watcher.connect('song-started', self.__song_started)
            widgets.watcher.connect('changed', self.__check_change)

        def __check_change(self, watcher, song):
            if song is watcher.song: self.__song_started(watcher, song)

        def __song_started(self, watcher, song):
            if song:
                t = self.__title(song)+self.__people(song)+self.__album(song)
            else: t = "<span size='xx-large'>%s</span>" % _("Not playing")
            self.set_markup(t)

        def __title(self, song):
            t = "<span weight='bold' size='large'>%s</span>" %(
                util.escape(song.comma("title")))
            if "version" in song:
                t += "\n<small><b>%s</b></small>" %(
                    util.escape(song.comma("version")))
            return t

        def __people(self, song):
            t = ""
            if "artist" in song:
                t += "\n" + _("by %s") % util.escape(song.comma("artist"))
            if "performer" in song:
                t += "\n<small>%s</small>" % util.escape(
                    _("Performed by %s") % song.comma("performer"))

            others = []
            for key, name in [
                ("arranger", _("arranged by %s")),
                ("lyricist", _("lyrics by %s")),
                ("conductor", _("conducted by %s")),
                ("composer", _("composed by %s")),
                ("author", _("written by %s"))]:
                if key in song: others.append(name % song.comma(key))
            if others:
                others = util.capitalize("; ".join(others))
                t += "\n<small>%s</small>" % util.escape(others)
            return t

        def __album(self, song):
            t = []
            if "album" in song:
                t.append("\n<b>%s</b>" % util.escape(song.comma("album")))
                if "discnumber" in song:
                    t.append(_("Disc %s") % util.escape(
                        song.comma("discnumber")))
                if "part" in song:
                    t.append("<b>%s</b>" % util.escape(song.comma("part")))
                if "tracknumber" in song:
                    t.append(_("Track %s") % util.escape(
                        song.comma("tracknumber")))
            return " - ".join(t)

    class PlayControls(gtk.Table):
        def __init__(self):
            gtk.Table.__init__(self, 2, 3)
            self.set_homogeneous(True)
            self.set_row_spacings(3)
            self.set_col_spacings(3)
            self.set_border_width(3)

            prev = gtk.Button()
            prev.add(gtk.image_new_from_stock(
                gtk.STOCK_MEDIA_PREVIOUS, gtk.ICON_SIZE_LARGE_TOOLBAR))
            prev.connect('clicked', self.__previous)
            self.attach(prev, 0, 1, 0, 1, xoptions=gtk.FILL, yoptions=gtk.FILL)

            play = gtk.Button()
            play.add(gtk.image_new_from_stock(
                gtk.STOCK_MEDIA_PLAY, gtk.ICON_SIZE_LARGE_TOOLBAR))
            play.connect('clicked', self.__playpause)
            self.attach(play, 1, 2, 0, 1, xoptions=gtk.FILL, yoptions=gtk.FILL)

            next = gtk.Button()
            next.add(gtk.image_new_from_stock(
                gtk.STOCK_MEDIA_NEXT, gtk.ICON_SIZE_LARGE_TOOLBAR))
            next.connect('clicked', self.__next)
            self.attach(next, 2, 3, 0, 1, xoptions=gtk.FILL, yoptions=gtk.FILL)

            tips = gtk.Tooltips()

            add = gtk.Button()
            add.add(gtk.image_new_from_stock(
                gtk.STOCK_ADD, gtk.ICON_SIZE_LARGE_TOOLBAR))
            add.connect('clicked', self.__add_music)
            self.attach(add, 0, 1, 1, 2, xoptions=gtk.FILL, yoptions=gtk.FILL)
            tips.set_tip(add, _("Add songs to your library"))

            prop = gtk.Button()
            prop.add(gtk.image_new_from_stock(
                gtk.STOCK_PROPERTIES, gtk.ICON_SIZE_LARGE_TOOLBAR))
            prop.connect('clicked', self.__properties)
            self.attach(prop, 1, 2, 1, 2, xoptions=gtk.FILL, yoptions=gtk.FILL)
            tips.set_tip(prop, _("View and edit tags in the playing song"))

            info = gtk.Button()
            info.add(gtk.image_new_from_stock(
                gtk.STOCK_DIALOG_INFO, gtk.ICON_SIZE_LARGE_TOOLBAR))
            info.connect('clicked', self.__website)
            self.attach(info, 2, 3, 1, 2, xoptions=gtk.FILL, yoptions=gtk.FILL)
            tips.set_tip(info, _("Visit the artist's website"))

            stopafter = MainWindow.StopAfterMenu()

            widgets.watcher.connect(
                'song-started', self.__song_started, stopafter,
                next, prop, info)
            widgets.watcher.connect(
                'song-ended', self.__song_ended, stopafter)
            widgets.watcher.connect(
                'paused', self.__paused, stopafter, play.child,
                gtk.STOCK_MEDIA_PLAY),
            widgets.watcher.connect(
                'unpaused', self.__paused, stopafter, play.child,
                gtk.STOCK_MEDIA_PAUSE),

            play.connect(
                'button-press-event', self.__popup_stopafter, stopafter)
            tips.enable()
            self.connect_object('destroy', gtk.Tooltips.destroy, tips)
            self.show_all()

        def __popup_stopafter(self, activator, event, stopafter):
            if event.button == 3:
                stopafter.popup(None, None, None, event.button, event.time)

        def __song_started(self, watcher, song, stopafter, *buttons):
            if song and stopafter.active:
                player.playlist.paused = True
            for b in buttons: b.set_sensitive(bool(song))

        def __paused(self, watcher, stopafter, image, stock):
            stopafter.active = False
            image.set_from_stock(stock, gtk.ICON_SIZE_LARGE_TOOLBAR)

        def __song_ended(self, watcher, song, stopped, stopafter):
            if stopped: stopafter.active = False

        def __playpause(self, button):
            if widgets.watcher.song is None: player.playlist.reset()
            else: player.playlist.paused ^= True

        def __previous(self, button): player.playlist.previous()
        def __next(self, button): player.playlist.next()

        def __add_music(self, button):
            chooser = FileChooser(
                widgets.main, _("Add Music"), widgets.main.last_dir)
            resp, fns = chooser.run()
            chooser.destroy()
            if resp == gtk.RESPONSE_OK:
                widgets.main.scan_dirs(fns)
                widgets.watcher.refresh()
            if fns: widgets.main.last_dir = fns[0]
            library.save(const.LIBRARY)

        def __properties(self, button):
            if widgets.watcher.song:
                SongProperties([widgets.watcher.song])

        def __website(self, button):
            song = widgets.watcher.song
            if not song: return
            website_wrap(button, song.website())

    class PositionSlider(gtk.HBox):
        __gsignals__ = {
            'seek': (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, (int,))
            }
                    
        def __init__(self):
            gtk.HBox.__init__(self)
            l = gtk.Label("0:00/0:00")
            l.set_padding(6, 0)
            self.pack_start(l, expand=False)
            scale = gtk.HScale(gtk.Adjustment(0, 0, 0, 3600, 15000, 0))
            scale.set_update_policy(gtk.UPDATE_DELAYED)
            scale.connect_object('adjust-bounds', self.emit, 'seek')
            scale.set_draw_value(False)
            self.pack_start(scale)

            widgets.watcher.connect(
                'song-started', self.__song_changed, scale, l)

            gobject.timeout_add(
                500, self.__update_time, widgets.watcher, scale, l)

        def __song_changed(self, watcher, song, position, label):
            if song:
                length = song["~#length"]
                position.set_range(0, length * 1000)
            else: position.set_range(0, 1)

        def __update_time(self, watcher, position, timer):
            cur, end = watcher.time
            position.set_value(cur)
            timer.set_text(
                "%d:%02d/%d:%02d" %
                (cur // 60000, (cur % 60000) // 1000,
                 end // 60000, (end % 60000) // 1000))
            return True

    gobject.type_register(PositionSlider)

    class VolumeSlider(gtk.VBox):
        def __init__(self, device):
            gtk.VBox.__init__(self)
            i = gtk.Image()
            i.set_from_pixbuf(gtk.gdk.pixbuf_new_from_file("volume.png"))
            self.pack_start(i, expand=False)
            slider = gtk.VScale(gtk.Adjustment(1, 0, 1, 0.01, 0.1))
            slider.set_update_policy(gtk.UPDATE_CONTINUOUS)
            slider.connect('value-changed', self.__volume_changed, device)
            self.pack_start(slider)
            tips = gtk.Tooltips()
            tips.set_tip(slider, _("Adjust audio volume"))            
            self.get_value = slider.get_value
            self.set_value = slider.set_value
            slider.set_inverted(True)
            slider.set_draw_value(False)
            self.set_value(config.getfloat("memory", "volume"))
            self.show_all()

        def __volume_changed(self, slider, device):
            val = (2 ** slider.get_value()) - 1
            device.volume = val
            config.set("memory", "volume", str(slider.get_value()))

    def __init__(self):
        gtk.Window.__init__(self)
        self.last_dir = os.path.expanduser("~")

        tips = gtk.Tooltips()
        self.set_title("Quod Libet")

        icon_theme = gtk.icon_theme_get_default()
        p = gtk.gdk.pixbuf_new_from_file("quodlibet.png")
        gtk.icon_theme_add_builtin_icon(const.ICON, 64, p)
        self.set_icon(icon_theme.load_icon(
            const.ICON, 64, gtk.ICON_LOOKUP_USE_BUILTIN))

        self.set_default_size(
            *map(int, config.get('memory', 'size').split()))
        self.add(gtk.VBox())

        # create main menubar, load/restore accelerator groups
        self._create_menu(tips)
        self.add_accel_group(self.ui.get_accel_group())
        gtk.accel_map_load(const.ACCELS)
        accelgroup = gtk.accel_groups_from_object(self)[0]
        accelgroup.connect('accel-changed',
                lambda *args: gtk.accel_map_save(const.ACCELS))
        self.child.pack_start(self.ui.get_widget("/Menu"), expand=False)

        # song info (top part of window)
        hbox = gtk.HBox()
        hbox.set_size_request(-1, 102)

        vbox = gtk.VBox()

        hb2 = gtk.HBox()

        # play controls
        t = self.PlayControls()
        hb2.pack_start(t, expand=False, fill=False)

        # song text
        text = self.SongInfo()
        hb2.pack_start(text)

        vbox.pack_start(hb2, expand=True)
        hbox.pack_start(vbox, expand=True)

        # position slider
        scale = self.PositionSlider()
        scale.connect('seek', lambda s, pos: player.playlist.seek(pos))
        vbox.pack_start(scale, expand=False)

        # cover image
        self.image = CoverImage()
        widgets.watcher.connect('song-started', self.image.set_song)
        hbox.pack_start(self.image, expand=False)

        # volume control
        self.volume = self.VolumeSlider(player.device)
        hbox.pack_start(self.volume, expand=False)

        self.child.pack_start(hbox, expand=False)

        # status area
        hbox = gtk.HBox(spacing=6)
        self.shuffle = shuffle = gtk.CheckButton(_("_Shuffle"))
        tips.set_tip(shuffle, _("Play songs in a random order"))
        shuffle.connect('toggled', self.toggle_shuffle)
        shuffle.set_active(config.getboolean('settings', 'shuffle'))
        hbox.pack_start(shuffle, expand=False)
        self.repeat = repeat = gtk.CheckButton(_("_Repeat"))
        repeat.connect('toggled', self.toggle_repeat)
        repeat.set_active(config.getboolean('settings', 'repeat'))
        tips.set_tip(
            repeat, _("Restart the playlist after all songs are played"))
        hbox.pack_start(repeat, expand=False)
        self.__statusbar = gtk.Label()
        self.__statusbar.set_text(_("No time information"))
        self.__statusbar.set_alignment(1.0, 0.5)
        self.__statusbar.set_justify(gtk.JUSTIFY_RIGHT)
        hbox.pack_start(self.__statusbar)
        hbox.set_border_width(3)
        self.child.pack_end(hbox, expand=False)

        # Set up the tray icon. It gets created even if we don't
        # actually use it (e.g. missing trayicon.so).
        self.icon = QLTrayIcon(self, self.volume)

        # song list
        self.song_scroller = sw = gtk.ScrolledWindow()
        sw.set_policy(gtk.POLICY_NEVER, gtk.POLICY_ALWAYS)
        sw.set_shadow_type(gtk.SHADOW_IN)
        self.songlist = MainSongList()
        self.songlist.set_rules_hint(True)
        sw.add(self.songlist)
        self.songlist.set_model(gtk.ListStore(object))
        SongList.set_all_column_headers(
            config.get("settings", "headers").split())
        sort = config.get('memory', 'sortby')
        self.songlist.set_sort_by(
            None, sort[1:], refresh=True, order=int(sort[0]))

        self.inter = gtk.VBox()

        # plugin support
        from plugins import PluginManager
        self.pm = PluginManager(widgets.watcher, [const.PLUGINS])
        self.pm.rescan()
        
        self.browser = None
        self.__select_browser(self, config.getint("memory", "browser"))
        self.browser.restore()
        self.browser.activate()

        self.open_fifo()
        self.__keys = MmKeys({"mm_prev": self.__previous_song,
                              "mm_next": self.__next_song,
                              "mm_playpause": self.__play_pause})

        self.child.show_all()
        self.showhide_playlist(self.ui.get_widget("/Menu/View/Songlist"))

        self.connect('configure-event', MainWindow.__save_size)
        self.connect('delete-event', MainWindow.__delete_event)
        self.connect_object('destroy', TrayIcon.destroy, self.icon)
        self.connect('destroy', gtk.main_quit)

        self.songlist.connect('row-activated', self.__select_song)
        self.songlist.connect('button-press-event', self.__songs_button_press)
        self.songlist.connect('key-press-event', self.__key_press)
        self.songlist.connect('popup-menu', self.__songs_popup_menu)
        self.songlist.connect('columns-changed', self.__cols_changed)

        widgets.watcher.connect('removed', self.__song_removed)
        widgets.watcher.connect('refresh', self.__set_time)
        widgets.watcher.connect('changed', self.__update_title)
        widgets.watcher.connect('song-started', self.__song_started)
        widgets.watcher.connect('song-ended', self.__song_ended)
        widgets.watcher.connect(
            'missing', self.__song_missing, self.__statusbar)
        widgets.watcher.connect('paused', self.__update_paused, True)
        widgets.watcher.connect('unpaused', self.__update_paused, False)

        self.resize(*map(int, config.get("memory", "size").split()))
        self.show()

    def __delete_event(self, event):
        if self.icon.enabled and config.getboolean("plugins", "icon_close"):
            self.icon.hide_window()
            return True

    def _create_menu(self, tips):
        ag = gtk.ActionGroup('MainWindowActions')
        ag.add_actions([
            ('Music', None, _("_Music")),
            ('AddMusic', gtk.STOCK_ADD, _('_Add Music...'), "<control>O", None,
             self.open_chooser),
            ('NewPlaylist', gtk.STOCK_EDIT, _('_New/Edit Playlist...'),
             None, None, self.__new_playlist),
            ('BrowseLibrary', gtk.STOCK_FIND, _('_Browse Library')),
            ("Preferences", gtk.STOCK_PREFERENCES, None, None, None,
             self.__preferences),
            ("Quit", gtk.STOCK_QUIT, None, None, None, gtk.main_quit),
            ('Filters', None, _("_Filters")),

            ("NotPlayedDay", gtk.STOCK_FIND, _("Not played to_day"),
             "", None, self.lastplayed_day),
            ("NotPlayedWeek", gtk.STOCK_FIND, _("Not played in a _week"),
             "", None, self.lastplayed_week),
            ("NotPlayedMonth", gtk.STOCK_FIND, _("Not played in a _month"),
             "", None, self.lastplayed_month),
            ("NotPlayedEver", gtk.STOCK_FIND, _("_Never played"),
             "", None, self.lastplayed_never),
            ("Top", gtk.STOCK_GO_UP, _("_Top 40"), "", None, self.__top40),
            ("Bottom", gtk.STOCK_GO_DOWN,_("B_ottom 40"), "",
             None, self.__bottom40),
            ("Song", None, _("S_ong")),
            ("Previous", gtk.STOCK_MEDIA_PREVIOUS, None, "<control>Left",
             None, self.__previous_song),
            ("PlayPause", gtk.STOCK_MEDIA_PLAY, None, "<control>space",
             None, self.__play_pause),
            ("Next", gtk.STOCK_MEDIA_NEXT, None, "<control>Right",
             None, self.__next_song),
            ("Properties", gtk.STOCK_PROPERTIES, None, "<Alt>Return", None,
             self.__current_song_prop),
            ("Rating", None, tag("rating")),

            ("Jump", gtk.STOCK_JUMP_TO, _("_Jump to playing song"),
             "<control>J", None, self.__jump_to_current),

            ("View", None, _("_View")),
            ("Help", None, _("_Help")),
            ("About", gtk.STOCK_ABOUT, None, None, None, AboutWindow),
            ])

        act = gtk.Action(
            "RefreshLibrary", _("Re_fresh Library"), None, gtk.STOCK_REFRESH)
        act.connect('activate', self.__rebuild)
        ag.add_action(act)
        act = gtk.Action(
            "ReloadLibrary", _("Re_load Library"), None, gtk.STOCK_REFRESH)
        act.connect('activate', self.__rebuild, True)
        ag.add_action(act)

        for tag_, lab in [
            ("genre", _("Filter on _genre")),
            ("artist", _("Filter on _artist")),
            ("album", _("Filter on al_bum"))]:
            act = gtk.Action(
                "Filter%s" % util.capitalize(tag_), lab, None, gtk.STOCK_INDEX)
            act.connect_object('activate', self.__filter_on, tag_)
            ag.add_action(act)

        for (tag_, accel, label) in [
            ("genre", "G", _("Random _genre")),
            ("artist", "T", _("Random _artist")),
            ("album", "M", _("Random al_bum"))]:
            act = gtk.Action("Random%s" % util.capitalize(tag_), label,
                             None, gtk.STOCK_DIALOG_QUESTION)
            act.connect('activate', self.__random, tag_)
            ag.add_action_with_accel(act, "<control>" + accel)

        ag.add_toggle_actions([
            ("Songlist", None, _("Song _List"), None, None,
             self.showhide_playlist,
             config.getboolean("memory", "songlist"))])

        ag.add_radio_actions([
            ("BrowserDisable", None, _("_Disable Browser"), None, None, 0),
            ("BrowserSearch", None, _("_Search Library"), None, None, 1),
            ("BrowserPlaylist", None, _("_Playlists"), None, None, 2),
            ("BrowserPaned", None, _("_Paned Browser"), None, None, 3),
            ("BrowserAlbum", None, _("_Album List"), None, None, 4),
            ], config.getint("memory", "browser"), self.__select_browser)

        for id, label, Kind in [
            ("BrowseSearch", _("_Search Library"), SearchBar),
            ("BrowsePaned", _("_Paned Browser"), PanedBrowser),
            ("BrowseAlbumList", _("_Album List"), AlbumList)]:
            act = gtk.Action(id, label, None, None)
            act.connect('activate', LibraryBrowser, Kind)
            ag.add_action(act)

        for i in range(5):
            act = gtk.Action(
                "Rate%d" % i, "%d %s" % (i, util.format_rating(i)), None, None)
            act.connect('activate', self.__set_rating, i)
            ag.add_action(act)
        
        self.ui = gtk.UIManager()
        self.ui.insert_action_group(ag, -1)
        self.ui.add_ui_from_string(const.MENU)

        # Cute. So. UIManager lets you attach tooltips, but when they're
        # for menu items, they just get ignored. So here I get to actually
        # attach them.
        tips.set_tip(
            self.ui.get_widget("/Menu/Music/RefreshLibrary"),
            _("Check for changes in the library made since the program "
              "was started"))
        tips.set_tip(
            self.ui.get_widget("/Menu/Music/ReloadLibrary"),
            _("Reload all songs in your library (this can take a long time)"))
        tips.set_tip(
            self.ui.get_widget("/Menu/Filters/Top"),
             _("The 40 songs you've played most (more than 40 may "
               "be chosen if there are ties)"))
        tips.set_tip(
            self.ui.get_widget("/Menu/Filters/Bottom"),
            _("The 40 songs you've played least (more than 40 may "
              "be chosen if there are ties)"))
        tips.set_tip(
            self.ui.get_widget("/Menu/Song/Properties"),
            _("View and edit tags in the playing song"))

    def __select_browser(self, activator, current):
        if not isinstance(current, int): current = current.get_current_value()
        config.set("memory", "browser", str(current))
        Browser = [EmptyBar, SearchBar, PlaylistBar, PanedBrowser,
                   AlbumList][current]
        if self.browser:
            c = self.child.get_children()[-2]
            c.remove(self.song_scroller)
            c.remove(self.browser)
            c.destroy()
            self.browser.destroy()
        self.browser = Browser(self.__browser_cb)
        if self.browser.expand:
            c = self.browser.expand()
            c.pack1(self.browser, resize=True)
            c.pack2(self.song_scroller, resize=True)
            c.show()
        else:
            c = gtk.VBox()
            c.pack_start(self.browser, expand=False)
            c.pack_start(self.song_scroller)
            c.show()
        self.child.pack_end(c)
        self.__hide_menus()
        self.__refresh_size()

    def open_fifo(self):
        try:
            if not os.path.exists(const.CONTROL):
                util.mkdir(const.DIR)
                os.mkfifo(const.CONTROL, 0600)
            self.fifo = os.open(const.CONTROL, os.O_NONBLOCK)
            gobject.io_add_watch(
                self.fifo, gtk.gdk.INPUT_READ, self.__input_check)
        except EnvironmentError: pass

    def __input_check(self, source, condition):
        c = os.read(source, 1)
        toggles = { "@": self.repeat, "&": self.shuffle }
        if c == "<": self.__previous_song()
        elif c == ">": self.__next_song()
        elif c == "-": self.__play_pause()
        elif c == ")": player.playlist.paused = False
        elif c == "|": player.playlist.paused = True
        elif c == "0": player.playlist.seek(0)
        elif c == "v":
            c2 = os.read(source, 3)
            if c2 == "+":
                self.volume.set_value(self.volume.get_value() + 0.05)
            elif c2 == "-":
                self.volume.set_value(self.volume.get_value() - 0.05)
            else:
                try: self.volume.set_value(int(c2) / 100.0)
                except ValueError: pass
        elif c in toggles:
            wid = toggles[c]
            c2 = os.read(source, 1)
            if c2 == "0": wid.set_active(False)
            elif c2 == "t": wid.set_active(not wid.get_active())
            else: wid.set_active(True)
        elif c == "!":
            if not self.get_property('visible'):
                self.move(*self.window_pos)
            self.present()
        elif c == "q": self.__make_query(os.read(source, 4096))
        elif c == "s":
            time = os.read(source, 20)
            seek_to = widgets.watcher.time[0]
            if time[0] == "+": seek_to += util.parse_time(time[1:]) * 1000
            elif time[0] == "-": seek_to -= util.parse_time(time[1:]) * 1000
            else: seek_to = util.parse_time(time) * 1000
            seek_to = min(widgets.watcher.time[1] - 1, max(0, seek_to))
            player.playlist.seek(seek_to)
        elif c == "p":
            filename = os.read(source, 4096)
            if library.add(filename):
                song = library[filename]
                if song not in player.playlist.get_playlist():
                    e_fn = sre.escape(filename)
                    self.__make_query("filename = /^%s/c" % e_fn)
                player.playlist.go_to(library[filename])
                player.playlist.paused = False
            else:
                print to(_("W: Unable to load %s") % filename)
        elif c == "d":
            filename = os.read(source, 4096)
            for added, changed, removed in library.scan([filename]): pass
            for song in added: widgets.watcher.added(song)
            for song in changed: widgets.watcher.changed(song)
            for song in removed: widgets.watcher.removed(song)
            self.__make_query("filename = /^%s/c" % sre.escape(filename))

        os.close(self.fifo)
        self.open_fifo()

    def __update_paused(self, watcher, paused):
        menu = self.ui.get_widget("/Menu/Song/PlayPause")
        if paused:
            menu.get_image().set_from_stock(
                gtk.STOCK_MEDIA_PLAY, gtk.ICON_SIZE_MENU)
            menu.child.set_text(_("_Play"))
        else:
            menu.get_image().set_from_stock(
                gtk.STOCK_MEDIA_PAUSE, gtk.ICON_SIZE_MENU)
            menu.child.set_text(_("_Pause"))
        menu.child.set_use_underline(True)

    def __song_missing(self, watcher, song, statusbar):
        statusbar.set_text(_("Could not play %s.") % song['~filename'])
        try: library.remove(song)
        except KeyError: pass
        else: watcher.removed(song)

    def __song_ended(self, watcher, song, stopped):
        if player.playlist.filter and not player.playlist.filter(song):
            player.playlist.remove(song)
            iter = self.songlist.song_to_iter(song)
            if iter: self.songlist.get_model().remove(iter)
            self.__set_time()

    def __update_title(self, watcher, song):
        if song is watcher.song:
            if song:
                self.set_title("Quod Libet - " + song.comma("~title~version"))
            else: self.set_title("Quod Libet")

    def __song_started(self, watcher, song):
        self.__update_title(watcher, song)

        for wid in ["Jump", "Next", "Properties", "FilterGenre",
                    "FilterArtist", "FilterAlbum", "Rating"]:
            self.ui.get_widget('/Menu/Song/' + wid).set_sensitive(bool(song))
        if song:
            for h in ['genre', 'artist', 'album']:
                self.ui.get_widget(
                    "/Menu/Song/Filter%s" % h.capitalize()).set_sensitive(
                    h in song)
        if song and config.getboolean("settings", "jump"):
            self.__jump_to_current()

    def __save_size(self, event):
        config.set("memory", "size", "%d %d" % (event.width, event.height))

    def __new_playlist(self, activator):
        options = map(PlayList.prettify_name, library.playlists())
        name = GetStringDialog(self, _("New/Edit Playlist"),
                               _("Enter a name for the new playlist. If it "
                                 "already exists it will be opened for "
                                 "editing."), options).run()
        if name:
            PlaylistWindow(name)

    def __refresh_size(self):
        if (not self.browser.expand and
            not self.song_scroller.get_property('visible')):
            width, height = self.get_size()
            self.resize(width, 1)
            self.set_geometry_hints(None, max_height=1, max_width=32000)
        else:
            self.set_geometry_hints(None, max_height=-1, max_width=-1)
        self.realize()

    def showhide_playlist(self, toggle):
        self.song_scroller.set_property('visible', toggle.get_active())
        config.set("memory", "songlist", str(toggle.get_active()))
        self.__refresh_size()

    def __play_pause(self, *args):
        if widgets.watcher.song is None: player.playlist.reset()
        else: player.playlist.paused ^= True

    def __jump_to_current(self, *args):
        watcher, songlist = widgets.watcher, self.songlist
        iter = songlist.song_to_iter(watcher.song)
        if iter:
            path = songlist.get_model().get_path(iter)
            songlist.scroll_to_cell(path, use_align=True, row_align=0.5)

    def __next_song(self, *args):
        player.playlist.next()

    def __previous_song(self, *args):
        player.playlist.previous()

    def toggle_repeat(self, button):
        player.playlist.repeat = button.get_active()
        config.set("settings", "repeat", str(bool(button.get_active())))

    def toggle_shuffle(self, button):
        player.playlist.shuffle = button.get_active()
        config.set("settings", "shuffle", str(bool(button.get_active())))

    def __random(self, item, key):
        if self.browser.can_filter(key):
            value = library.random(key)
            if value is not None: self.browser.filter(key, [value])

    def lastplayed_day(self, menuitem):
        self.__make_query("#(lastplayed > today)")
    def lastplayed_week(self, menuitem):
        self.__make_query("#(lastplayed > 7 days ago)")
    def lastplayed_month(self, menuitem):
        self.__make_query("#(lastplayed > 30 days ago)")
    def lastplayed_never(self, menuitem):
        self.__make_query("#(playcount = 0)")

    def __top40(self, menuitem):
        songs = [song["~#playcount"] for song in library.values()]
        if len(songs) == 0: return
        songs.sort()
        if len(songs) < 40:
            self.__make_query("#(playcount > %d)" % (songs[0] - 1))
        else:
            self.__make_query("#(playcount > %d)" % (songs[-40] - 1))

    def __bottom40(self, menuitem):
        songs = [song["~#playcount"] for song in library.values()]
        if len(songs) == 0: return
        songs.sort()
        if len(songs) < 40:
            self.__make_query("#(playcount < %d)" % (songs[0] + 1))
        else:
            self.__make_query("#(playcount < %d)" % (songs[-40] + 1))

    def __rebuild(self, activator, hard=False):
        window = qltk.WaitLoadWindow(self, len(library) // 7,
                                     _("Quod Libet is scanning your library. "
                                       "This may take several minutes.\n\n"
                                       "%d songs reloaded\n%d songs removed"),
                                     (0, 0))
        iter = 7
        c = r = 0
        for c, r in library.rebuild(hard):
            if iter == 7:
                if window.step(len(c), len(r)):
                    window.destroy()
                    break
                iter = 0
            iter += 1
        else:
            window.destroy()
            if config.get("settings", "scan"):
                self.scan_dirs(config.get("settings", "scan").split(":"))
        for song in c: widgets.watcher.changed(song)
        for song in r: widgets.watcher.removed(song)
        if c or r:
            library.save(const.LIBRARY)
            player.playlist.refilter()
            self.songlist.refresh()
        widgets.watcher.refresh()

    # Set up the preferences window.
    def __preferences(self, activator):
        if not hasattr(widgets, 'preferences'):
            widgets.preferences = PreferencesWindow(self)
        widgets.preferences.present()

    def __select_song(self, songlist, indices, col):
        model = songlist.get_model()
        iter = model.get_iter(indices)
        song = model.get_value(iter, 0)
        player.playlist.go_to(song)
        player.playlist.paused = False

    def open_chooser(self, *args):
        chooser = FileChooser(self, _("Add Music"), self.last_dir)
        resp, fns = chooser.run()
        chooser.destroy()
        if resp == gtk.RESPONSE_OK:
            self.scan_dirs(fns)
            widgets.watcher.refresh()
        if fns: self.last_dir = fns[0]
        library.save(const.LIBRARY)

    def scan_dirs(self, fns):
        win = qltk.WaitLoadWindow(self, 0,
                                  _("Quod Libet is scanning for new songs and "
                                    "adding them to your library.\n\n"
                                    "%d songs added"), 0)
        for added, changed, removed in library.scan(fns):
            if win.step(len(added)): break
        for song in changed: widgets.watcher.changed(song)
        for song in added: widgets.watcher.added(song)
        for song in removed: widgets.watcher.removed(song)
        win.destroy()
        player.playlist.refilter()
        self.songlist.refresh()

    def __songs_button_press(self, view, event):
        x, y = map(int, [event.x, event.y])
        try: path, col, cellx, celly = view.get_path_at_pos(x, y)
        except TypeError: return True
        view.grab_focus()
        selection = view.get_selection()
        header = col.header_name
        if event.button == 3:
            if not selection.path_is_selected(path):
                view.set_cursor(path, col, 0)
            self.prep_main_popup(header, event.button, event.time)
            return True
        elif event.button == 1 and header == "~rating":
            width = col.get_property('width')
            song = view.get_model()[path][0]
            parts = (width / 4.0)
            if cellx < parts + 1:
                rating = (song["~#rating"] & 1) ^ 1
            elif cellx < 2*parts: rating = 2
            elif cellx < 3*parts: rating = 3
            else: rating = 4
            self.__set_rating(col, rating, [song])

    def __songs_popup_menu(self, songlist):
        path, col = songlist.get_cursor()
        header = col.header_name
        self.prep_main_popup(header, 1, 0)

    def remove_song(self, item):
        view = self.songlist
        selection = view.get_selection()
        model, rows = selection.get_selected_rows()
        iters = [model.get_iter(row) for row in rows]
        for iter in iters:
            song = model[iter][0]
            library.remove(song)
            widgets.watcher.removed(song)
        widgets.watcher.refresh()

    def __song_removed(self, watcher, song):
        player.playlist.remove(song)
        self.__set_time()

    def delete_song(self, item):
        view = self.songlist
        selection = view.get_selection()
        model, rows = selection.get_selected_rows()
        songs = [(model[r][0]["~filename"], model[r][0],
                  model.get_iter(r)) for r in rows]
        d = DeleteDialog(self, [song[0] for song in songs])
        resp = d.run()
        d.destroy()
        if resp == 1 or resp == gtk.RESPONSE_DELETE_EVENT: return
        else:
            if resp == 0: s = _("Moving %d/%d.")
            elif resp == 2: s = _("Deleting %d/%d.")
            else: return
            w = qltk.WaitLoadWindow(self, len(songs), s, (0, len(songs)))
            trash = os.path.expanduser("~/.Trash")
            for filename, song, iter in songs:
                try:
                    if resp == 0:
                        basename = os.path.basename(filename)
                        shutil.move(filename, os.path.join(trash, basename))
                    else:
                        os.unlink(filename)
                    library.remove(song)
                    widgets.watcher.removed(song)

                except:
                    qltk.ErrorMessage(
                        self, _("Unable to delete file"),
                        _("Deleting <b>%s</b> failed. "
                          "Possibly the target file does not exist, "
                          "or you do not have permission to "
                          "delete it.") % (filename)).run()
                    break
                else:
                    w.step(w.current + 1, w.count)
            w.destroy()
            widgets.watcher.refresh()

    def __current_song_prop(self, *args):
        song = widgets.watcher.song
        if song: SongProperties([song])

    def song_properties(self, item):
        SongProperties(self.songlist.get_selected_songs())

    def __set_rating(self, item, value, songs=[]):
        songs = songs or [widgets.watcher.song]
        if songs[0] is None: return
        for song in songs:
            song["~#rating"] = value
            widgets.watcher.changed(song)

    def set_selected_ratings(self, item, value):
        self.__set_rating(item, value, self.songlist.get_selected_songs())

    def __key_press(self, songlist, event):
        if event.string in ["0", "1", "2", "3", "4"]:
            self.set_selected_ratings(songlist, int(event.string))

    def prep_main_popup(self, header, button, time):
        if "~" in header[1:]: header = header.lstrip("~").split("~")[0]
        menu = gtk.Menu()

        if header == "~rating":
            item = gtk.MenuItem(_("Set Rating"))
            m2 = gtk.Menu()
            item.set_submenu(m2)
            for i in range(5):
                itm = gtk.MenuItem("%d\t%s" %(i, util.format_rating(i)))
                m2.append(itm)
                itm.connect('activate', self.set_selected_ratings, i)
            menu.append(item)
            menu.append(gtk.SeparatorMenuItem())

        songs = self.songlist.get_selected_songs()

        if self.browser.can_filter("artist"):
            b = gtk.ImageMenuItem(_("Filter on _artist"))
            b.connect_object('activate', self.__filter_on, 'artist', songs)
            b.get_image().set_from_stock(gtk.STOCK_INDEX, gtk.ICON_SIZE_MENU)
            menu.append(b)
        if self.browser.can_filter("album"):
            b = gtk.ImageMenuItem(_("Filter on al_bum"))
            b.connect_object('activate', self.__filter_on, 'album', songs)
            b.get_image().set_from_stock(gtk.STOCK_INDEX, gtk.ICON_SIZE_MENU)
            menu.append(b)
        header = {"~rating":"~#rating", "~length":"~#length"}.get(
            header, header)
        if (header not in ["artist", "album"] and
            self.browser.can_filter(header) and
            (header[0] != "~" or header[1] == "#")):
            b = gtk.ImageMenuItem(_("_Filter on %s") % tag(header, False))
            b.connect_object('activate', self.__filter_on, header, songs)
            b.get_image().set_from_stock(gtk.STOCK_INDEX, gtk.ICON_SIZE_MENU)
            menu.append(b)
        if menu.get_children(): menu.append(gtk.SeparatorMenuItem())

        submenu = gtk.Menu()
        if self.__create_plugins_menu(self.pm, submenu):
            b = gtk.ImageMenuItem(_("Plugins"))
            b.get_image().set_from_stock(gtk.STOCK_EXECUTE, gtk.ICON_SIZE_MENU)
            menu.append(b)
            b.set_submenu(submenu)
            if menu.get_children(): menu.append(gtk.SeparatorMenuItem())

        b = gtk.ImageMenuItem(gtk.STOCK_REMOVE)
        b.connect('activate', self.remove_song)
        menu.append(b)
        b = gtk.ImageMenuItem(gtk.STOCK_DELETE)
        b.connect('activate', self.delete_song)
        menu.append(b)
        b = gtk.ImageMenuItem(gtk.STOCK_PROPERTIES)
        b.connect_object('activate', SongProperties, songs)
        menu.append(b)

        menu.show_all()
        menu.connect('selection-done', lambda m: m.destroy())
        menu.popup(None, None, None, button, time)

    def __create_plugins_menu(self, pm, menu):
        for child in menu.get_children(): menu.remove(child)
        songs = self.songlist.get_selected_songs()
        plugins = [(plugin.PLUGIN_NAME, plugin) for plugin in pm.list(songs)]
        plugins.sort()
        for name, plugin in plugins:
            if hasattr(plugin, 'PLUGIN_ICON'):
                b = gtk.ImageMenuItem(name)
                b.get_image().set_from_stock(plugin.PLUGIN_ICON,
                    gtk.ICON_SIZE_MENU)
            else:
                b = gtk.MenuItem(name)
            b.connect('activate', self.__invoke_plugin, pm, plugin, songs)
            menu.append(b)

        if menu.get_children():
            menu.show_all()
            return True
        else:
            menu.destroy()
            return False

    def __invoke_plugin(self, event, pm, plugin, songs):
        pm.invoke(plugin, songs)

    def __hide_menus(self):
        menus = {'genre': ["/Menu/Song/FilterGenre",
                           "/Menu/Filters/RandomGenre"],
                 'artist': ["/Menu/Song/FilterArtist",
                           "/Menu/Filters/RandomArtist"],
                 'album':  ["/Menu/Song/FilterAlbum",
                           "/Menu/Filters/RandomAlbum"],
                 None: ["/Menu/Filters/NotPlayedDay",
                        "/Menu/Filters/NotPlayedWeek",
                        "/Menu/Filters/NotPlayedMonth",
                        "/Menu/Filters/NotPlayedEver",
                        "/Menu/Filters/Top",
                        "/Menu/Filters/Bottom"]}
        for key, widgets in menus.items():
            c = self.browser.can_filter(key)
            for widget in widgets:
                self.ui.get_widget(widget).set_property('visible', c)

    def __browser_cb(self, text, sort):
        if isinstance(text, str): text = text.decode("utf-8")
        text = text.strip()
        if self.browser.background:
            try: bg = config.get("browsers", "background").decode('utf-8')
            except UnicodeError: bg = ""
        else: bg = ""
        if player.playlist.playlist_from_filters(text, bg):
            if sort:
                self.songlist.set_sort_by(None, tag=sort, refresh=False)
            self.songlist.refresh()
            self.__set_time()

    def __filter_on(self, header, songs=None):
        if not self.browser or not self.browser.can_filter(header):
            return
        if songs is None:
            if widgets.watcher.song: songs = [widgets.watcher.song]
            else: return

        values = set()
        if header.startswith("~#"):
            values.update([song(header, 0) for song in songs])
        else:
            for song in songs: values.update(song.list(header))
        self.browser.filter(header, list(values))

    def __cols_changed(self, songlist):
        headers = [col.header_name for col in songlist.get_columns()]
        if len(headers) == len(config.get("settings", "headers").split()):
            # Not an addition or removal (handled separately)
            config.set("settings", "headers", " ".join(headers))

    def __make_query(self, query):
        if self.browser.can_filter(None):
            self.browser.set_text(query.encode('utf-8'))
            self.browser.activate()

    def __set_time(self, watcher=None):
        statusbar = self.__statusbar
        model = self.songlist.get_model()
        songs = [row[0] for row in model]
        i = len(songs)
        length = sum([song["~#length"] for song in songs])
        if i != 1: statusbar.set_text(
            _("%d songs (%s)") % (i, util.format_time_long(length)))
        else: statusbar.set_text(
            _("%d song (%s)") % (i, util.format_time_long(length)))

class SongList(HintedTreeView):
    """Wrap a treeview that works like a songlist"""
    songlistviews = {}
    headers = []

    def __init__(self, recall=0):
        HintedTreeView.__init__(self)
        self.set_size_request(200, 150)
        self.set_rules_hint(True)
        self.get_selection().set_mode(gtk.SELECTION_MULTIPLE)
        self.songlistviews[self] = None     # register self
        self.recall_size = recall
        self.set_column_headers(self.headers)
        self.connect_object('destroy', SongList._destroy, self)
        sigs = [widgets.watcher.connect('changed', self.__song_updated),
                widgets.watcher.connect('song-started', self.__song_updated),
                widgets.watcher.connect('removed', self.__song_removed),
                widgets.watcher.connect('paused', self.__redraw_current),
                widgets.watcher.connect('unpaused', self.__redraw_current)
                ]
        for sig in sigs:
            self.connect_object('destroy', widgets.watcher.disconnect, sig)

    def __redraw_current(self, watcher):
        model = self.get_model()
        iter = self.song_to_iter(watcher.song)
        if iter: model[iter][0] = model[iter][0]

    def set_all_column_headers(cls, headers):
        cls.headers = headers
        for listview in cls.songlistviews:
            listview.set_column_headers(headers)
    set_all_column_headers = classmethod(set_all_column_headers)

    def get_selected_songs(self):
        model, rows = self.get_selection().get_selected_rows()
        return [model[row][0] for row in rows]

    def song_to_iter(self, song):
        model = self.get_model()
        it = [None]
        def find(model, path, iter, it):
            if model[iter][0] == song: it.append(iter)
            return bool(it[-1])
        model.foreach(find, it)
        return it[-1]

    def __song_updated(self, watcher, song):
        iter = self.song_to_iter(song)
        if iter:
            model = self.get_model()
            model[iter][0] = model[iter][0]

    def __song_removed(self, watcher, song):
        iter = self.song_to_iter(song)
        if iter:
            model = self.get_model()
            model.remove(iter)

    # Build a new filter around our list model, set the headers to their
    # new values.
    def set_column_headers(self, headers):
        if len(headers) == 0: return
        SHORT_COLS = ["tracknumber", "discnumber", "~length", "~rating"]

        for c in self.get_columns(): self.remove_column(c)

        def cell_data_current(column, cell, model, iter,
                pixbuf=(gtk.STOCK_MEDIA_PLAY, gtk.STOCK_MEDIA_PAUSE)):
            try:
                if model[iter][0] is not widgets.watcher.song: stock = ''
                else: stock = pixbuf[player.playlist.paused]
                cell.set_property('stock-id', stock)
            except AttributeError: pass

        def cell_data(column, cell, model, iter,
                attr = (pango.WEIGHT_NORMAL, pango.WEIGHT_BOLD)):
            try:
                song = model[iter][0]
                cell.set_property('text', song.comma(column.header_name))
            except AttributeError: pass

        def cell_data_fn(column, cell, model, iter, code,
                attr = (pango.WEIGHT_NORMAL, pango.WEIGHT_BOLD)):
            try:
                song = model[iter][0]
                cell.set_property('text', util.unexpand(
                    song.comma(column.header_name).decode(code, 'replace')))
            except AttributeError: pass

        # indicator column
        render = gtk.CellRendererPixbuf()
        render.set_property('xalign', 0.5)
        column = gtk.TreeViewColumn("", render)
        column.header_name = "~current"
        column.set_sizing(gtk.TREE_VIEW_COLUMN_GROW_ONLY)
        column.set_cell_data_func(render, cell_data_current)
        self.append_column(column)
        if "~current" in headers: headers.remove("~current")

        for i, t in enumerate(headers):
            render = gtk.CellRendererText()
            title = tag(t)
            column = gtk.TreeViewColumn(title, render)
            column.header_name = t
            if t in SHORT_COLS or t.startswith("~#"):
                column.set_sizing(gtk.TREE_VIEW_COLUMN_GROW_ONLY)
            else:
                render.set_property('ellipsize', pango.ELLIPSIZE_END)
                column.set_expand(True)
                column.set_resizable(True)
                column.set_sizing(gtk.TREE_VIEW_COLUMN_FIXED)
                column.set_fixed_width(1)
            if hasattr(self, 'set_sort_by'):
                column.connect('clicked', self.set_sort_by)
            self._set_column_settings(column)
            if t in ["~filename", "~basename", "~dirname"]:
                column.set_cell_data_func(render, cell_data_fn,
                                          util.fscoding())
            else:
                column.set_cell_data_func(render, cell_data)
            if t == "~length":
                column.set_alignment(1.0)
                render.set_property('xalign', 1.0)
            elif t.startswith("~#"):
                render.set_property('xalign', 1.0)
                render.set_property('xpad', 12)
                
            self.append_column(column)

    def _destroy(self):
        del(self.songlistviews[self])
        self.set_model(None)

    def _set_column_settings(self, column):
        column.set_visible(True)

class LibraryBrowser(gtk.Window):
    def __init__(self, activator, Kind=SearchBar):
        gtk.Window.__init__(self)
        self.set_border_width(12)
        self.set_title(_("Library Browser"))
        icon_theme = gtk.icon_theme_get_default()
        self.set_icon(icon_theme.load_icon(
            const.ICON, 64, gtk.ICON_LOOKUP_USE_BUILTIN))

        view = SongList()
        view.set_model(gtk.ListStore(object))
        self.__view = view

        sw = gtk.ScrolledWindow()
        sw.set_shadow_type(gtk.SHADOW_IN)
        sw.add(view)
        sw.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)

        browser = Kind(self.__search, save=False, play=False)
        if Kind.expand:
            container = Kind.expand()
            container.pack1(browser, resize=True)
            container.pack2(sw, resize=True)
            self.add(container)
        else:
            vbox = gtk.VBox(spacing=6)
            vbox.pack_start(browser, expand=False)
            vbox.pack_start(sw)
            self.add(vbox)

        menu = gtk.Menu()
        rem = gtk.ImageMenuItem(gtk.STOCK_REMOVE, gtk.ICON_SIZE_MENU)
        rem.connect('activate', self.__remove_selected_songs, view)
        menu.append(rem)
        prop = gtk.ImageMenuItem(gtk.STOCK_PROPERTIES, gtk.ICON_SIZE_MENU)
        prop.connect('activate', self.__song_properties, view)
        menu.append(prop)
        menu.show_all()
        self.connect_object('destroy', gtk.Menu.destroy, menu)
        view.connect('button-press-event', self.__button_press, menu)
        view.connect_object('popup-menu', gtk.Menu.popup, menu,
                            None, None, None, 2, 0)

        self.set_default_size(500, 300)
        self.show_all()

    def __remove_selected_songs(self, activator, view):
        model, rows = view.get_selection().get_selected_rows()
        for row in rows:
            library.remove(model[row][0])
            widgets.watcher.removed(model[row][0])

    def __song_properties(self, activator, view):
        model, rows = view.get_selection().get_selected_rows()
        SongProperties([model[row][0] for row in rows])

    def __button_press(self, view, event, menu):
        if event.button != 3:
            return False
        x, y = map(int, [event.x, event.y])
        try: path, col, cellx, celly = view.get_path_at_pos(x, y)
        except TypeError: return True
        view.grab_focus()
        selection = view.get_selection()
        if not selection.path_is_selected(path):
            view.set_cursor(path, col, 0)
        menu.popup(None, None, None, event.button, event.time)
        return True

    def __search(self, text, dummy):
        model = self.__view.get_model()
        model.clear()
        if isinstance(text, str): text = text.decode("utf-8")
        if text is None: songs = library.values()
        else: songs = library.query(text)
        songs.sort()
        for song in songs: model.append([song])

class PlayList(SongList):
    # ["%", " "] + parser.QueryLexeme.table.keys()
    BAD = ["%", " ", "!", "&", "|", "(", ")", "=", ",", "/", "#", ">", "<"]
    DAB = BAD[::-1]

    def normalize_name(name):
        for c in PlayList.BAD: name = name.replace(c, "%"+hex(ord(c))[2:])
        return name
    normalize_name = staticmethod(normalize_name)

    def prettify_name(name):
        for c in PlayList.DAB: name = name.replace("%"+hex(ord(c))[2:], c)
        return name
    prettify_name = staticmethod(prettify_name)

    def lists_model(cls):
        try: return cls._lists_model
        except AttributeError:
            model = cls._lists_model = gtk.ListStore(str, str)
            playlists = [[PlayList.prettify_name(p), p] for p in
                          library.playlists()]
            playlists.sort()
            model.append([(_("All songs")), ""])
            for p in playlists: model.append(p)
            return model
    lists_model = classmethod(lists_model)

    def __init__(self, name):
        plname = 'playlist_' + PlayList.normalize_name(name)
        self.__key = key = '~#' + plname
        model = gtk.ListStore(object)
        super(PlayList, self).__init__(400)

        for song in library.query('#(%s > 0)' % plname, sort=key):
            model.append([song])

        self.set_model(model)
        self.connect_object('drag-end', self.__refresh_indices, key)
        self.set_reorderable(True)

        menu = gtk.Menu()
        rem = gtk.ImageMenuItem(gtk.STOCK_REMOVE, gtk.ICON_SIZE_MENU)
        rem.connect('activate', self.__remove_selected_songs, key)
        menu.append(rem)
        prop = gtk.ImageMenuItem(gtk.STOCK_PROPERTIES, gtk.ICON_SIZE_MENU)
        prop.connect('activate', self.__song_properties)
        menu.append(prop)
        menu.show_all()
        self.connect_object('destroy', gtk.Menu.destroy, menu)
        self.connect('button-press-event', self.__button_press, menu)
        self.connect_object('popup-menu', gtk.Menu.popup, menu,
                            None, None, None, 2, 0)

        sig = widgets.watcher.connect('refresh', self.__refresh_indices, key)
        self.connect_object('destroy', widgets.watcher.disconnect, sig)

    def append_songs(self, songs):
        model = self.get_model()
        current_songs = set([row[0]['~filename'] for row in model])
        for song in songs:
            if song['~filename'] not in current_songs:
                model.append([song])
                song[self.__key] = len(model) # 1 based index; 0 means out

    def __remove_selected_songs(self, activator, key):
        model, rows = self.get_selection().get_selected_rows()
        rows.sort()
        rows.reverse()
        for row in rows:
            del(model[row][0][key])
            iter = model.get_iter(row)
            model.remove(iter)
        self.__refresh_indices(activator, key)

    def __song_properties(self, activator):
        model, rows = self.get_selection().get_selected_rows()
        SongProperties([model[row][0] for row in rows])

    def __refresh_indices(self, context, key):
        for i, row in enumerate(iter(self.get_model())):
            row[0][self.__key] = i + 1    # 1 indexed; 0 is not present

    def __button_press(self, view, event, menu):
        if event.button != 3:
            return False
        x, y = map(int, [event.x, event.y])
        try: path, col, cellx, celly = view.get_path_at_pos(x, y)
        except TypeError: return True
        view.grab_focus()
        selection = view.get_selection()
        if not selection.path_is_selected(path):
            view.set_cursor(path, col, 0)
        menu.popup(None, None, None, event.button, event.time)
        return True

class MainSongList(SongList):

    def _set_column_settings(self, column):
        column.set_clickable(True)
        column.set_reorderable(True)
        column.set_sort_indicator(False)

    # Resort based on the header clicked.
    def set_sort_by(self, header, tag=None, refresh=True, order=None):
        s = gtk.SORT_ASCENDING
        if header and tag is None: tag = header.header_name

        for h in self.get_columns():
            if h.header_name == tag:
                if order is None:
                    s = header.get_sort_order()
                    if (not header.get_sort_indicator() or
                        s == gtk.SORT_DESCENDING):
                        s = gtk.SORT_ASCENDING
                    else: s = gtk.SORT_DESCENDING
                else:
                    if order: s == gtk.SORT_ASCENDING
                    else: s = gtk.SORT_DESCENDING
                h.set_sort_indicator(True)
                h.set_sort_order(s)
            else: h.set_sort_indicator(False)
        config.set('memory', 'sortby', "%d%s" % (s == gtk.SORT_ASCENDING,
                                                 tag))
        if tag == "~album~part": tag = "album"
        player.playlist.sort_by(tag, s == gtk.SORT_DESCENDING)
        if refresh: self.refresh()

    # Clear the songlist and readd the songs currently wanted.
    def refresh(self):
        model = self.get_model()

        selected = self.get_selected_songs()
        selected = dict.fromkeys([song['~filename'] for song in selected])

        model.clear()
        for song in player.playlist:
            model.append([song])

        # reselect what we can
        selection = self.get_selection()
        for i, row in enumerate(iter(model)):
            if row[0]['~filename'] in selected:
                selection.select_path(i)

class GetStringDialog(gtk.Dialog):
    def __init__(self, parent, title, text, options=[]):
        gtk.Dialog.__init__(self, title, parent)
        self.set_border_width(6)        
        self.set_resizable(False)
        self.add_buttons(gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                         gtk.STOCK_OPEN, gtk.RESPONSE_OK)
        self.vbox.set_spacing(6)
        self.set_default_response(gtk.RESPONSE_OK)

        box = gtk.VBox(spacing=6)
        lab = gtk.Label(text)
        box.set_border_width(6)
        lab.set_line_wrap(True)
        lab.set_justify(gtk.JUSTIFY_CENTER)
        box.pack_start(lab)

        if options:
            self.__entry = gtk.combo_box_entry_new_text()
            for o in options: self.__entry.append_text(o)
            self.__val = self.__entry.child
            box.pack_start(self.__entry)
        else:
            self.__val = gtk.Entry()
            box.pack_start(self.__val)
        self.vbox.pack_start(box)
        self.child.show_all()

    def run(self):
        self.show()
        self.__val.set_text("")
        self.__val.set_activates_default(True)
        self.__val.grab_focus()
        resp = gtk.Dialog.run(self)
        if resp == gtk.RESPONSE_OK:
            value = self.__val.get_text()
        else: value = None
        self.destroy()
        return value

class AddTagDialog(gtk.Dialog):
    def __init__(self, parent, can_change, validators):
        if can_change == True:
            can = ["title", "version", "artist", "album",
                   "performer", "discnumber", "tracknumber"]
        else: can = can_change
        can.sort()

        gtk.Dialog.__init__(self, _("Add a new tag"), parent)
        self.set_border_width(6)
        self.set_resizable(False)
        self.add_button(gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL)
        add = self.add_button(gtk.STOCK_ADD, gtk.RESPONSE_OK)
        self.vbox.set_spacing(6)
        self.set_default_response(gtk.RESPONSE_OK)
        table = gtk.Table(2, 2)
        table.set_row_spacings(12)
        table.set_col_spacings(6)
        table.set_border_width(6)

        if can_change == True:
            self.__tag = gtk.combo_box_entry_new_text()
            for tag in can: self.__tag.append_text(tag)
        else:
            self.__tag = gtk.combo_box_new_text()
            for tag in can: self.__tag.append_text(tag)
            self.__tag.set_active(0)

        label = gtk.Label()
        label.set_alignment(0.0, 0.5)
        label.set_text(_("_Tag:"))
        label.set_use_underline(True)
        label.set_mnemonic_widget(self.__tag)
        table.attach(label, 0, 1, 0, 1)
        table.attach(self.__tag, 1, 2, 0, 1)

        self.__val = gtk.Entry()
        label = gtk.Label()
        label.set_text(_("_Value:"))
        label.set_alignment(0.0, 0.5)
        label.set_use_underline(True)
        label.set_mnemonic_widget(self.__val)
        valuebox = gtk.EventBox()
        table.attach(label, 0, 1, 1, 2)
        table.attach(valuebox, 1, 2, 1, 2)
        hbox = gtk.HBox()
        valuebox.add(hbox)
        hbox.pack_start(self.__val)
        hbox.set_spacing(6)
        invalid = gtk.image_new_from_stock(
            gtk.STOCK_DIALOG_WARNING, gtk.ICON_SIZE_SMALL_TOOLBAR)
        hbox.pack_start(invalid)

        self.vbox.pack_start(table)
        self.child.show_all()
        invalid.hide()

        tips = gtk.Tooltips()
        for entry in [self.__tag, self.__val]:
            entry.connect(
                'changed', self.__validate, validators, add, invalid, tips,
                valuebox)

    def get_tag(self):
        try: return self.__tag.child.get_text().lower().strip()
        except AttributeError:
            return self.__tag.get_model()[self.__tag.get_active()][0]

    def get_value(self):
        return self.__val.get_text().decode("utf-8")

    def __validate(self, editable, validators, add, invalid, tips, box):
        tag = self.get_tag()
        try: validator, message = validators.get(tag)
        except TypeError: valid = True
        else: valid = bool(validator(self.get_value()))

        add.set_sensitive(valid)
        if valid:
            invalid.hide()
            tips.disable()
        else:
            invalid.show()
            tips.set_tip(box, message)
            tips.enable()

    def run(self):
        self.show()
        try: self.__tag.child.set_activates_default(True)
        except AttributeError: pass
        self.__val.set_activates_default(True)
        self.__tag.grab_focus()
        return gtk.Dialog.run(self)

class SongProperties(gtk.Window):
    __gsignals__ = { 'changed': (gobject.SIGNAL_RUN_LAST,
                                 gobject.TYPE_NONE, (object,))
                     }

    class Information(gtk.ScrolledWindow):
        class SongInfo(gtk.VBox):
            def __init__(self, spacing=6, border=12, library=True, songs=[]):
                gtk.VBox.__init__(self, spacing=spacing)
                self.set_border_width(border)
                attrs = ["title", "album", "people", "description", "file"]
                songs = songs[:]
                songs.sort()
                if library: attrs.append("library")
                for attr in attrs:
                    attr = "_" + attr
                    if hasattr(self, attr):
                        getattr(self, attr)(songs)
                self.show_all()

            def Label(self, *args):
                l = gtk.Label(*args)
                l.set_selectable(True)
                l.set_alignment(0, 0)
                return l

            def pack_frame(self, name, widget, expand=False):
                f = gtk.Frame()
                f.set_shadow_type(gtk.SHADOW_NONE)
                l = gtk.Label()
                l.set_markup("<u><b>%s</b></u>" % name)
                f.set_label_widget(l)
                a = gtk.Alignment(xalign=0, yalign=0, xscale=1, yscale=1)
                a.set_padding(3, 0, 12, 0)
                f.add(a)
                a.add(widget)
                self.pack_start(f, expand=expand)

            def _show_big_cover(self, image, event, song):
                if (event.button == 1 and
                    event.type == gtk.gdk._2BUTTON_PRESS):
                    cover = song.find_cover()
                    try: BigCenteredImage(song.comma("album"), cover.name)
                    except: pass

            def _make_cover(self, cover, song):
                p = gtk.gdk.pixbuf_new_from_file_at_size(cover.name, 70, 70)
                i = gtk.Image()
                i.set_from_pixbuf(p)
                ev = gtk.EventBox()
                ev.add(i)
                ev.connect(
                    'button-press-event', self._show_big_cover, song)
                f = gtk.Frame()
                f.set_shadow_type(gtk.SHADOW_ETCHED_OUT)
                f.add(ev)
                return f

        class NoSongs(SongInfo):
            def _description(self, songs):
                self.pack_start(gtk.Label(_("No songs are selected.")))

        class OneSong(SongInfo):
            def _title(self, (song,)):
                l = self.Label()
                text = "<big><b>%s</b></big>" % util.escape(song("title"))
                if "version" in song:
                    text += "\n" + util.escape(song.comma("version"))
                l.set_markup(text)
                l.set_ellipsize(pango.ELLIPSIZE_END)
                self.pack_start(l, expand=False)

            def _album(self, (song,)):
                if "album" not in song: return
                w = self.Label("")
                text = []
                text.append("<i>%s</i>" % util.escape(song.comma("album")))
                if "date" in song:
                    text[-1] += " (%s)" % util.escape(song.comma("date"))
                secondary = []
                if "discnumber" in song:
                    secondary.append(_("Disc %s") % song["discnumber"])
                if "part" in song:
                    secondary.append("<i>%s</i>" %
                                     util.escape(song.comma("part")))
                if "tracknumber" in song:
                    secondary.append(_("Track %s") % song["tracknumber"])
                if secondary: text.append(" - ".join(secondary))

                if "organization" in song or "labelid" in song:
                    t = util.escape(song.comma("~organization~labelid"))
                    text.append(t)

                if "producer" in song:
                    text.append("Produced by %s" %(
                        util.escape(song.comma("producer"))))

                w.set_markup("\n".join(text))
                w.set_ellipsize(pango.ELLIPSIZE_END)
                cover = song.find_cover()
                if cover:
                    hb = gtk.HBox(spacing=12)
                    try:
                        hb.pack_start(
                            self._make_cover(cover, song), expand=False)
                    except:
                        hb.destroy()
                        self.pack_frame(tag("album"), w)
                    else:
                        hb.pack_start(w)
                        self.pack_frame(tag("album"), hb)
                else: self.pack_frame(tag("album"), w)

            def _people(self, (song,)):
                vb = SongProperties.Information.SongInfo(3, 0)
                if "artist" in song:
                    if "\n" in song["artist"]:
                        title = util.capitalize(_("artists"))
                    else:
                        title = tag("artist")
                    l = self.Label(song["artist"])
                    l.set_ellipsize(pango.ELLIPSIZE_END)
                    vb.pack_start(l)
                else: title = _("People")
                for names, tag_ in [
                    (_("performers"), "performer"),
                    (_("lyricists"),  "lyricist"),
                    (_("arrangers"),  "arranger"),
                    (_("composers"),  "composer"),
                    (_("conductors"), "conductor"),
                    (_("authors"),    "author")]:
                    if tag_ in song:
                        l = self.Label(song[tag_])
                        l.set_ellipsize(pango.ELLIPSIZE_END)
                        if "\n" in song[tag_]:
                            vb.pack_frame(util.capitalize(names), l)
                        else:
                            vb.pack_frame(tag(tag_), l)
                if not vb.get_children(): vb.destroy()
                else: self.pack_frame(title, vb)

            def _library(self, (song,)):
                def counter(i):
                    if i == 0: return _("Never")
                    elif i == 1: return _("1 time")
                    else: return _("%d times") % i
                def ftime(t):
                    if t == 0: return _("Unknown")
                    else: return time.strftime("%c", time.localtime(t))

                playcount = counter(song.get("~#playcount", 0))
                skipcount = counter(song.get("~#skipcount", 0))
                lastplayed = ftime(song.get("~#lastplayed", 0))
                if lastplayed == _("Unknown"):
                    lastplayed = _("Never")
                added = ftime(song.get("~#added", 0))
                rating = song("~rating")

                t = gtk.Table(5, 2)
                t.set_col_spacings(6)
                t.set_homogeneous(False)
                table = [(_("added"), added),
                         (_("last played"), lastplayed),
                         (_("play count"), playcount),
                         (_("skip count"), skipcount),
                         (_("rating"), rating)]

                for i, (l, r) in enumerate(table):
                    l = "<b>%s</b>" % util.capitalize(util.escape(l) + ":")
                    lab = self.Label()
                    lab.set_markup(l)
                    t.attach(lab, 0, 1, i + 1, i + 2, xoptions=gtk.FILL)
                    t.attach(self.Label(r), 1, 2, i + 1, i + 2)
                    
                self.pack_frame(_("Library"), t)

            def _file(self, (song,)):
                def ftime(t):
                    if t == 0: return _("Unknown")
                    else: return time.strftime("%c", time.localtime(t))

                fn = util.fsdecode(util.unexpand(song["~filename"]))
                length = util.format_time_long(song["~#length"])
                size = util.format_size(os.path.getsize(song["~filename"]))
                mtime = ftime(util.mtime(song["~filename"]))
                if "~#bitrate" in song and song["~#bitrate"] != 0:
                    bitrate = _("%d kbps") % int(song["~#bitrate"]/1000)
                else: bitrate = False

                t = gtk.Table(4, 2)
                t.set_col_spacings(6)
                t.set_homogeneous(False)
                table = [(_("length"), length),
                         (_("file size"), size),
                         (_("modified"), mtime)]
                if bitrate:
                    table.insert(1, (_("bitrate"), bitrate))
                fnlab = self.Label(fn)
                fnlab.set_ellipsize(pango.ELLIPSIZE_MIDDLE)
                t.attach(fnlab, 0, 2, 0, 1, xoptions=gtk.FILL)
                for i, (l, r) in enumerate(table):
                    l = "<b>%s</b>" % util.capitalize(util.escape(l) + ":")
                    lab = self.Label()
                    lab.set_markup(l)
                    t.attach(lab, 0, 1, i + 1, i + 2, xoptions=gtk.FILL)
                    t.attach(self.Label(r), 1, 2, i + 1, i + 2)
                    
                self.pack_frame(_("File"), t)

        class OneAlbum(SongInfo):
            def _title(self, songs):
                song = songs[0]
                l = self.Label()
                l.set_ellipsize(pango.ELLIPSIZE_END)
                text = "<big><b>%s</b></big>" % util.escape(song["album"])
                if "date" in song: text += "\n" + song["date"]
                l.set_markup(text)
                self.pack_start(l, expand=False)

            def _album(self, songs):
                text = []

                discs = {}
                for song in songs:
                    try:
                        discs[song("~#disc")] = int(
                            song["tracknumber"].split("/")[1])
                    except (AttributeError, ValueError, IndexError, KeyError):
                        discs[song("~#disc")] = max([
                            song("~#track", discs.get(song("~#disc"), 0))])
                tracks = sum(discs.values())
                discs = len(discs)
                length = sum([song["~#length"] for song in songs])

                if tracks == 0 or tracks < len(songs): tracks = len(songs)

                parts = []
                if discs > 1: parts.append(_("%d discs") % discs)
                parts.append(_("%d tracks") % tracks)
                if tracks != len(songs):
                    parts.append(_("%d selected") % len(songs))

                text.append(", ".join(parts))
                text.append(util.format_time_long(length))

                if "location" in song:
                    text.append(util.escape(song["location"]))
                if "organization" in song or "labelid" in song:
                    t = util.escape(song.comma("~organization~labelid"))
                    text.append(t)

                if "producer" in song:
                    text.append(_("Produced by %s") %(
                        util.escape(song.comma("producer"))))

                w = self.Label("")
                w.set_ellipsize(pango.ELLIPSIZE_END)
                w.set_markup("\n".join(text))
                cover = song.find_cover()
                if cover:
                    hb = gtk.HBox(spacing=12)
                    try:
                        hb.pack_start(
                            self._make_cover(cover, song), expand=False)
                    except:
                        hb.destroy()
                        self.pack_start(w, expand=False)
                    else:
                        hb.pack_start(w)
                        self.pack_start(hb, expand=False)
                else: self.pack_start(w, expand=False)

            def _people(self, songs):
                artists = set([])
                performers = set([])
                for song in songs:
                    artists.update(song.list("artist"))
                    performers.update(song.list("performer"))

                artists = list(artists); artists.sort()
                performers = list(performers); performers.sort()

                if artists:
                    if len(artists) == 1: title = tag("artist")
                    else: title = util.capitalize(_("artists"))
                    self.pack_frame(title, self.Label("\n".join(artists)))
                if performers:
                    if len(performers) == 1: title = tag("performer")
                    else: title = util.capitalize(_("performers"))
                    self.pack_frame(title, self.Label("\n".join(performers)))

            def _description(self, songs):
                text = []
                cur_disc = songs[0]("~#disc", 1) - 1
                cur_part = None
                cur_track = songs[0]("~#track", 1) - 1
                for song in songs:
                    track = song("~#track", 0)
                    disc = song("~#disc", 0)
                    part = song.get("part")
                    if disc != cur_disc:
                        if cur_disc: text.append("")
                        cur_track = song("~#track", 1) - 1
                        cur_part = None
                        cur_disc = disc
                        if disc:
                            text.append("<b>%s</b>" % (_("Disc %s") % disc))
                    if part != cur_part:
                        ts = "    " * bool(disc)
                        cur_part = part
                        if part:
                            text.append("%s<b>%s</b>" %(ts, util.escape(part)))
                    cur_track += 1
                    ts = "    " * (bool(disc) + bool(part))
                    while cur_track < track:
                        text.append("%s<b>%d.</b> <i>%s</i>" %(
                            ts, cur_track, _("No information available")))
                        cur_track += 1
                    text.append("%s<b>%d.</b> %s" %(
                        ts, track, util.escape(song.comma("~title~version"))))
                l = self.Label()
                l.set_markup("\n".join(text))
                l.set_ellipsize(pango.ELLIPSIZE_END)
                self.pack_frame(_("Track List"), l)

        class OneArtist(SongInfo):
            def _title(self, songs):
                l = self.Label()
                l.set_ellipsize(pango.ELLIPSIZE_END)
                artist = util.escape(songs[0]("artist"))
                l.set_markup("<b><big>%s</big></b>" % artist)
                self.pack_start(l, expand=False)

            def _album(self, songs):
                noalbum = 0
                albums = {}
                for song in songs:
                    if "album" in song:
                        albums[song.list("album")[0]] = song
                    else: noalbum += 1
                albums = [(song.get("date"), song, album) for
                          album, song in albums.items()]
                albums.sort()
                def format((date, song, album)):
                    if date: return "%s (%s)" % (album, date[:4])
                    else: return album
                covers = [(a, s.find_cover(), s) for d, s, a in albums]
                albums = map(format, albums)
                if noalbum:
                    albums.append(_("%d songs with no album") % noalbum)
                l = self.Label("\n".join(albums))
                l.set_ellipsize(pango.ELLIPSIZE_END)
                self.pack_frame(_("Selected Discography"), l)

                tips = gtk.Tooltips()
                covers = [ac for ac in covers if bool(ac[1])]
                t = gtk.Table(4, (len(covers) // 4) + 1)
                t.set_col_spacings(12)
                t.set_row_spacings(12)
                added = set()
                for i, (album, cover, song) in enumerate(covers):
                    if cover.name in added: continue
                    try:
                        cov = self._make_cover(cover, song)
                        tips.set_tip(cov.child, album)
                        c = i % 4
                        r = i // 4
                        t.attach(cov, c, c + 1, r, r + 1,
                                 xoptions=gtk.EXPAND, yoptions=0)
                    except: pass
                    added.add(cover.name)
                self.pack_start(t, expand=False)
                self.connect_object('destroy', gtk.Tooltips.destroy, tips)

        class ManySongs(SongInfo):
            def _title(self, songs):
                l = self.Label()
                t = _("%d songs") % len(songs)
                l.set_markup("<big><b>%s</b></big>" % t)
                self.pack_start(l, expand=False)

            def _people(self, songs):
                artists = set([])
                none = 0
                for song in songs:
                    if "artist" in song: artists.update(song.list("artist"))
                    else: none += 1
                artists = list(artists)
                artists.sort()
                num_artists = len(artists)

                if none: artists.append(_("%d songs with no artist") % none)
                self.pack_frame(
                    "%s (%d)" % (util.capitalize(_("artists")), num_artists),
                    self.Label("\n".join(artists)))

            def _album(self, songs):
                albums = set([])
                none = 0
                for song in songs:
                    if "album" in song: albums.update(song.list("album"))
                    else: none += 1
                albums = list(albums)
                albums.sort()
                num_albums = len(albums)

                if none: albums.append(_("%d songs with no album") % none)
                self.pack_frame(
                    "%s (%d)" % (util.capitalize(_("albums")), num_albums),
                    self.Label("\n".join(albums)))

            def _file(self, songs):
                time = 0
                size = 0
                for song in songs:
                    time += song["~#length"]
                    try: size += os.path.getsize(song["~filename"])
                    except EnvironmentError: pass
                table = gtk.Table(2, 2)
                table.set_col_spacings(6)
                table.attach(self.Label(_("Total length:")), 0, 1, 0, 1,
                             xoptions=gtk.FILL)
                table.attach(
                    self.Label(util.format_time_long(time)), 1, 2, 0, 1)
                table.attach(self.Label(_("Total size:")), 0, 1, 1, 2,
                             xoptions=gtk.FILL)
                table.attach(self.Label(util.format_size(size)), 1, 2, 1, 2)
                self.pack_frame(_("Files"), table)

        def __init__(self, parent, library):
            gtk.ScrolledWindow.__init__(self)
            self.title = _("Information")
            self.set_policy(gtk.POLICY_NEVER, gtk.POLICY_AUTOMATIC)
            self.add(gtk.Viewport())
            self.child.set_shadow_type(gtk.SHADOW_NONE)
            self.tips = gtk.Tooltips()
            parent.connect_object(
                'changed', self.__class__.__update, self, library)

        def __update(self, songs, library):
            if self.child.child: self.child.child.destroy()
            
            if len(songs) == 0: Ctr = self.NoSongs
            elif len(songs) == 1: Ctr = self.OneSong
            else:
                albums = [song.get("album") for song in songs]
                artists = [song.get("artist") for song in songs]
                if min(albums) == max(albums) and None not in albums:
                    Ctr = self.OneAlbum
                elif min(artists) == max(artists) and None not in artists:
                    Ctr = self.OneArtist
                else: Ctr = self.ManySongs
            self.child.add(Ctr(library=library, songs=songs))

    class EditTags(gtk.VBox):
        def __init__(self, parent):
            gtk.VBox.__init__(self, spacing=12)
            self.title = _("Edit Tags")
            self.set_border_width(12)
            self.prop = parent

            self.model = gtk.ListStore(str, str, bool, bool, bool, str)
            self.view = HintedTreeView(self.model)
            selection = self.view.get_selection()
            selection.connect('changed', self.tag_select)
            render = gtk.CellRendererPixbuf()
            column = gtk.TreeViewColumn(_("Write"), render)

            style = self.view.get_style()
            pixbufs = [ style.lookup_icon_set(stock)
                        .render_icon(style, gtk.TEXT_DIR_NONE, state,
                            gtk.ICON_SIZE_MENU, self.view, None)
                        for state in (gtk.STATE_INSENSITIVE, gtk.STATE_NORMAL)
                            for stock in (gtk.STOCK_EDIT, gtk.STOCK_DELETE) ]
            def cdf_write(col, rend, model, iter, (write, delete)):
                row = model[iter]
                rend.set_property('pixbuf', pixbufs[2*row[write]+row[delete]])
            column.set_cell_data_func(render, cdf_write, (2, 4))
            self.view.connect('button-press-event',
                              self.write_toggle, (column, 2))
            self.view.append_column(column)

            render = gtk.CellRendererText()
            render.connect('edited', self.edit_tag, self.model, 0)
            column = gtk.TreeViewColumn(_('Tag'), render, text=0,
                                        strikethrough=4)
            column.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
            self.view.append_column(column)

            render = gtk.CellRendererText()
            render.set_property('ellipsize', pango.ELLIPSIZE_END)
            render.set_property('editable', True)
            render.connect('edited', self.edit_tag, self.model, 1)
            render.markup = 1
            column = gtk.TreeViewColumn(_('Value'), render, markup=1,
                                        editable=3, strikethrough=4)
            column.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
            self.view.append_column(column)

            self.view.connect('popup-menu', self.popup_menu)
            self.view.connect('button-press-event', self.button_press)

            sw = gtk.ScrolledWindow()
            sw.set_shadow_type(gtk.SHADOW_IN)
            sw.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
            sw.add(self.view)
            self.pack_start(sw)

            self.buttonbox = gtk.HBox(spacing=18)
            bbox1 = gtk.HButtonBox()
            bbox1.set_spacing(6)
            bbox1.set_layout(gtk.BUTTONBOX_START)
            self.add = gtk.Button(stock=gtk.STOCK_ADD)
            self.add.connect('clicked', self.add_tag)
            self.remove = gtk.Button(stock=gtk.STOCK_REMOVE)
            self.remove.connect('clicked', self.remove_tag)
            self.remove.set_sensitive(False)
            bbox1.pack_start(self.add)
            bbox1.pack_start(self.remove)

            bbox2 = gtk.HButtonBox()
            bbox2.set_spacing(6)
            bbox2.set_layout(gtk.BUTTONBOX_END)
            self.revert = gtk.Button(stock=gtk.STOCK_REVERT_TO_SAVED)
            self.revert.connect('clicked', self.revert_files)
            self.save = gtk.Button(stock=gtk.STOCK_SAVE)
            self.save.connect('clicked', self.save_files)
            self.revert.set_sensitive(False)
            self.save.set_sensitive(False)
            bbox2.pack_start(self.revert)
            bbox2.pack_start(self.save)

            self.buttonbox.pack_start(bbox1)
            self.buttonbox.pack_start(bbox2)

            self.pack_start(self.buttonbox, expand=False)

            tips = gtk.Tooltips()
            for widget, tip in [
                (self.view, _("Double-click a tag value to change it, "
                              "right-click for other options")),
                (self.add, _("Add a new tag to the file")),
                (self.remove, _("Remove a tag from the file"))]:
                tips.set_tip(widget, tip)
            parent.connect_object('changed', self.__class__.__update, self)

        def popup_menu(self, view):
            path, col = view.get_cursor()
            row = view.get_model()[path]
            self.show_menu(row, 1, 0)

        def button_press(self, view, event):
            if event.button not in (2, 3): return False
            x, y = map(int, [event.x, event.y])
            try: path, col, cellx, celly = view.get_path_at_pos(x, y)
            except TypeError: return True
            view.grab_focus()
            selection = view.get_selection()
            if not selection.path_is_selected(path):
                view.set_cursor(path, col, 0)
            row = view.get_model()[path]

            if event.button == 2: # middle click paste
                if col != view.get_columns()[2]: return False
                display = gtk.gdk.display_manager_get().get_default_display()
                clipboard = gtk.Clipboard(display, "PRIMARY")
                for rend in col.get_cell_renderers():
                    if rend.get_property('editable'):
                        clipboard.request_text(self._paste, (rend, path[0]))
                        return True
                else: return False

            elif event.button == 3: # right click menu
                self.show_menu(row, event.button, event.time)
                return True

        def _paste(self, clip, text, (rend, path)):
            if text: rend.emit('edited', path, text.strip())

        def split_into_list(self, activator):
            model, iter = self.view.get_selection().get_selected()
            row = model[iter]
            spls = config.get("settings", "splitters")
            vals = util.split_value(util.unescape(row[1]), spls)
            if vals[0] != util.unescape(row[1]):
                row[1] = util.escape(vals[0])
                row[2] = True
                for val in vals[1:]: self.add_new_tag(row[0], val)

        def split_title(self, activator):
            model, iter = self.view.get_selection().get_selected()
            row = model[iter]
            spls = config.get("settings", "splitters")
            title, versions = util.split_title(util.unescape(row[1]), spls)
            if title != util.unescape(row[1]):
                row[1] = util.escape(title)
                row[2] = True
                for val in versions: self.add_new_tag("version", val)

        def split_album(self, activator):
            model, iter = self.view.get_selection().get_selected()
            row = model[iter]
            album, disc = util.split_album(util.unescape(row[1]))
            if album != util.unescape(row[1]):
                row[1] = util.escape(album)
                row[2] = True
                self.add_new_tag("discnumber", disc)

        def split_people(self, tag):
            model, iter = self.view.get_selection().get_selected()
            row = model[iter]
            spls = config.get("settings", "splitters")
            person, others = util.split_people(util.unescape(row[1]), spls)
            if person != util.unescape(row[1]):
                row[1] = util.escape(person)
                row[2] = True
                for val in others: self.add_new_tag(tag, val)

        def split_performer(self, activator): self.split_people("performer")
        def split_arranger(self, activator): self.split_people("arranger")

        def show_menu(self, row, button, time):
            menu = gtk.Menu()        
            spls = config.get("settings", "splitters")

            b = gtk.ImageMenuItem(_("_Split into multiple values"))
            b.get_image().set_from_stock(gtk.STOCK_FIND_AND_REPLACE,
                                         gtk.ICON_SIZE_MENU)
            b.set_sensitive(len(util.split_value(row[1], spls)) > 1)
            b.connect('activate', self.split_into_list)
            menu.append(b)
            menu.append(gtk.SeparatorMenuItem())

            if row[0] == "album":
                b = gtk.ImageMenuItem(_("Split disc out of _album"))
                b.get_image().set_from_stock(gtk.STOCK_FIND_AND_REPLACE,
                                             gtk.ICON_SIZE_MENU)
                b.connect('activate', self.split_album)
                b.set_sensitive(util.split_album(row[1])[1] is not None)
                menu.append(b)

            elif row[0] == "title":
                b = gtk.ImageMenuItem(_("Split version out of title"))
                b.get_image().set_from_stock(gtk.STOCK_FIND_AND_REPLACE,
                                             gtk.ICON_SIZE_MENU)
                b.connect('activate', self.split_title)
                b.set_sensitive(util.split_title(row[1], spls)[1] != [])
                menu.append(b)

            elif row[0] == "artist":
                ok = (util.split_people(row[1], spls)[1] != [])

                b = gtk.ImageMenuItem(_("Split arranger out of ar_tist"))
                b.get_image().set_from_stock(gtk.STOCK_FIND_AND_REPLACE,
                                             gtk.ICON_SIZE_MENU)
                b.connect('activate', self.split_arranger)
                b.set_sensitive(ok)
                menu.append(b)

                b = gtk.ImageMenuItem(_("Split _performer out of artist"))
                b.get_image().set_from_stock(gtk.STOCK_FIND_AND_REPLACE,
                                             gtk.ICON_SIZE_MENU)
                b.connect('activate', self.split_performer)
                b.set_sensitive(ok)
                menu.append(b)

            if len(menu.get_children()) > 2:
                menu.append(gtk.SeparatorMenuItem())

            b = gtk.ImageMenuItem(gtk.STOCK_REMOVE, gtk.ICON_SIZE_MENU)
            b.connect('activate', self.remove_tag)
            menu.append(b)

            menu.show_all()
            menu.connect('selection-done', lambda m: m.destroy())
            menu.popup(None, None, None, button, time)

        def tag_select(self, selection):
            model, iter = selection.get_selected()
            self.remove.set_sensitive(bool(selection.count_selected_rows())
                                      and model[iter][3])

        def add_new_tag(self, comment, value):
            edited = True
            edit = True
            orig = None
            deleted = False
            iters = []
            def find_same_comments(model, path, iter):
                if model[path][0] == comment: iters.append(iter)
            self.model.foreach(find_same_comments)
            row = [comment, util.escape(value), edited, edit,deleted,orig]
            if len(iters): self.model.insert_after(iters[-1], row=row)
            else: self.model.append(row=row)
            self.save.set_sensitive(True)
            self.revert.set_sensitive(True)

        def add_tag(self, *args):
            add = AddTagDialog(self.prop, self.songinfo.can_change(),
                {'date': [sre.compile(r"^\d{4}(-\d{2}-\d{2})?$").match,
                _("The date must be entered in YYYY or YYYY-MM-DD format.")]})

            while True:
                resp = add.run()
                if resp != gtk.RESPONSE_OK: break
                comment = add.get_tag()
                value = add.get_value()
                if not self.songinfo.can_change(comment):
                    qltk.ErrorMessage(
                        self.prop, _("Invalid tag"),
                        _("Invalid tag <b>%s</b>\n\nThe files currently"
                          " selected do not support editing this tag.")%
                        util.escape(comment)).run()
                else:
                    self.add_new_tag(comment, value)
                    break

            add.destroy()

        def remove_tag(self, *args):
            model, iter = self.view.get_selection().get_selected()
            row = model[iter]
            if row[0] in self.songinfo:
                row[2] = True # Edited
                row[4] = True # Deleted
            else:
                model.remove(iter)
            self.save.set_sensitive(True)
            self.revert.set_sensitive(True)

        def save_files(self, *args):
            updated = {}
            deleted = {}
            added = {}
            def create_property_dict(model, path, iter):
                row = model[iter]
                # Edited, and or and not Deleted
                if row[2] and not row[4]:
                    if row[5] is not None:
                        updated.setdefault(row[0], [])
                        updated[row[0]].append((util.decode(row[1]),
                                                util.decode(row[5])))
                    else:
                        added.setdefault(row[0], [])
                        added[row[0]].append(util.decode(row[1]))
                if row[2] and row[4]:
                    if row[5] is not None:
                        deleted.setdefault(row[0], [])
                        deleted[row[0]].append(util.decode(row[5]))
            self.model.foreach(create_property_dict)

            win = WritingWindow(self.prop, len(self.songs))
            for song in self.songs:
                if not song.valid() and not qltk.ConfirmAction(
                    self.prop, _("Tag may not be accurate"),
                    _("<b>%s</b> looks like it was changed while the "
                      "program was running. Saving now without "
                      "refreshing your library might overwrite other "
                      "changes to tag.\n\n"
                      "Save this tag anyway?") % song("~basename")
                    ).run():
                    break

                changed = False
                for key, values in updated.iteritems():
                    for (new_value, old_value) in values:
                        new_value = util.unescape(new_value)
                        if song.can_change(key):
                            if old_value is None: song.add(key, new_value)
                            else: song.change(key, old_value, new_value)
                            changed = True
                for key, values in added.iteritems():
                    for value in values:
                        value = util.unescape(value)
                        if song.can_change(key):
                            song.add(key, value)
                            changed = True
                for key, values in deleted.iteritems():
                    for value in values:
                        value = util.unescape(value)
                        if song.can_change(key) and key in song:
                            song.remove(key, value)
                            changed = True

                if changed:
                    try: song.write()
                    except:
                        qltk.ErrorMessage(
                            self.prop, _("Unable to edit song"),
                            _("Saving <b>%s</b> failed. The file "
                              "may be read-only, corrupted, or you "
                              "do not have permission to edit it.")%(
                            util.escape(song('~basename')))).run()
                        widgets.watcher.error(song)
                        break
                    widgets.watcher.changed(song)

                if win.step(): break

            win.destroy()
            widgets.watcher.refresh()
            self.save.set_sensitive(False)
            self.revert.set_sensitive(False)

        def revert_files(self, *args):
            self.__update(self.songs)

        def edit_tag(self, renderer, path, new, model, colnum):
            new = ', '.join(new.splitlines())
            row = model[path]
            date = sre.compile("^\d{4}(-\d{2}-\d{2})?$")
            if row[0] == "date" and not date.match(new):
                qltk.WarningMessage(self.prop, _("Invalid date format"),
                                    _("Invalid date: <b>%s</b>.\n\n"
                                      "The date must be entered in YYYY or "
                                      "YYYY-MM-DD format.") % new).run()
            elif row[colnum].replace('<i>','').replace('</i>','') != new:
                row[colnum] = util.escape(new)
                row[2] = True # Edited
                row[4] = False # not Deleted
                self.save.set_sensitive(True)
                self.revert.set_sensitive(True)

        def write_toggle(self, view, event, (writecol, edited)):
            if event.button != 1: return False
            x, y = map(int, [event.x, event.y])
            try: path, col, cellx, celly = view.get_path_at_pos(x, y)
            except TypeError: return False

            if col is writecol:
                row = view.get_model()[path]
                row[edited] = not row[edited]
                self.save.set_sensitive(True)
                self.revert.set_sensitive(True)
                return True

        def __update(self, songs):
            from library import AudioFileGroup
            self.songinfo = songinfo = AudioFileGroup(songs)
            self.songs = songs
            self.view.set_model(None)
            self.model.clear()
            self.view.set_model(self.model)

            keys = songinfo.realkeys()
            keys.sort()

            if not config.state("allcomments"):
                machine_comments = set(['replaygain_album_gain',
                                        'replaygain_album_peak',
                                        'replaygain_track_gain',
                                        'replaygain_track_peak'])
                keys = filter(lambda k: k not in machine_comments, keys)

            # reverse order here so insertion puts them in proper order.
            for comment in ['album', 'artist', 'title']:
                try: keys.remove(comment)
                except ValueError: pass
                else: keys.insert(0, comment)

            for comment in keys:
                orig_value = songinfo[comment].split("\n")
                value = songinfo[comment].safenicestr()
                edited = False
                edit = songinfo.can_change(comment)
                deleted = False
                for i, v in enumerate(value.split("\n")):
                    self.model.append(row=[comment, v, edited, edit, deleted,
                                           orig_value[i]])

            self.buttonbox.set_sensitive(bool(songinfo.can_change()))
            self.remove.set_sensitive(False)
            self.save.set_sensitive(False)
            self.revert.set_sensitive(False)
            self.add.set_sensitive(bool(songs))
            self.songs = songs

    class TagByFilename(gtk.VBox):
        def __init__(self, prop):
            gtk.VBox.__init__(self, spacing=6)
            self.title = _("Tag by Filename")
            self.set_border_width(12)
            hbox = gtk.HBox(spacing=12)

            # Main buttons
            preview = qltk.Button(_("_Preview"), gtk.STOCK_CONVERT)
            save = gtk.Button(stock=gtk.STOCK_SAVE)

            # Text entry and preview button
            combo = qltk.ComboBoxEntrySave(
                const.TBP, const.TBP_EXAMPLES.split("\n"))
            hbox.pack_start(combo)
            entry = combo.child
            hbox.pack_start(preview, expand=False)
            self.pack_start(hbox, expand=False)

            # Header preview display
            view = gtk.TreeView()
            sw = gtk.ScrolledWindow()
            sw.set_shadow_type(gtk.SHADOW_IN)
            sw.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
            sw.add(view)
            self.pack_start(sw)

            # Options
            vbox = gtk.VBox()
            space = gtk.CheckButton(_("Replace _underscores with spaces"))
            space.set_active(config.state("tbp_space"))
            titlecase = gtk.CheckButton(_("_Title-case resulting values"))
            titlecase.set_active(config.state("titlecase"))
            split = gtk.CheckButton(_("Split into _multiple values"))
            split.set_active(config.state("splitval"))
            addreplace = gtk.combo_box_new_text()
            addreplace.append_text(_("Tags should replace existing ones"))
            addreplace.append_text(_("Tags should be added to existing ones"))
            addreplace.set_active(config.getint("settings", "addreplace"))
            for i in [space, titlecase, split]:
                vbox.pack_start(i)
            vbox.pack_start(addreplace)
            self.pack_start(vbox, expand=False)

            # Save button
            bbox = gtk.HButtonBox()
            bbox.set_layout(gtk.BUTTONBOX_END)
            bbox.pack_start(save)
            self.pack_start(bbox, expand=False)

            tips = gtk.Tooltips()
            tips.set_tip(
                titlecase,
                _("The first letter of each word will be capitalized"))

            # Changing things -> need to preview again
            kw = { "titlecase": titlecase,
                   "splitval": split, "tbp_space": space }
            for i in [space, titlecase, split]:
                i.connect('toggled', self.__changed, preview, save, kw)
            entry.connect('changed', self.__changed, preview, save, kw)

            UPDATE_ARGS = [prop, view, combo, entry, preview, save,
                           space, titlecase, split]

            # Song selection changed, preview clicked
            preview.connect('clicked', self.__preview_tags, *UPDATE_ARGS)
            prop.connect_object(
                'changed', self.__class__.__update, self, *UPDATE_ARGS)

            # Save changes
            save.connect('clicked', self.__save_files, prop, view, entry,
                         addreplace)

        def __update(self, songs, parent, view, combo, entry, preview, save,
                     space, titlecase, split):
            from library import AudioFileGroup
            self.__songs = songs

            songinfo = AudioFileGroup(songs)
            if songs: pattern_text = entry.get_text().decode("utf-8")
            else: pattern_text = ""
            try: pattern = util.PatternFromFile(pattern_text)
            except sre.error:
                qltk.ErrorMessage(
                    parent, _("Invalid pattern"),
                    _("The pattern\n\t<b>%s</b>\nis invalid. "
                      "Possibly it contains the same tag twice or "
                      "it has unbalanced brackets (&lt; / &gt;).")%(
                    util.escape(pattern_text))).run()
                return
            else:
                if pattern_text:
                    combo.prepend_text(pattern_text)
                    combo.write(const.TBP)

            invalid = []

            for header in pattern.headers:
                if not songinfo.can_change(header):
                    invalid.append(header)
            if len(invalid) and songs:
                if len(invalid) == 1:
                    title = _("Invalid tag")
                    msg = _("Invalid tag <b>%s</b>\n\nThe files currently"
                            " selected do not support editing this tag.")
                else:
                    title = _("Invalid tags")
                    msg = _("Invalid tags <b>%s</b>\n\nThe files currently"
                            " selected do not support editing these tags.")

                qltk.ErrorMessage(parent, title,
                                  msg % ", ".join(invalid)).run()
                pattern = util.PatternFromFile("")

            view.set_model(None)
            rep = space.get_active()
            title = titlecase.get_active()
            split = split.get_active()
            model = gtk.ListStore(object, str,
                                 *([str] * len(pattern.headers)))
            for col in view.get_columns():
                view.remove_column(col)

            col = gtk.TreeViewColumn(_('File'), gtk.CellRendererText(),
                                     text=1)
            col.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
            view.append_column(col)
            for i, header in enumerate(pattern.headers):
                render = gtk.CellRendererText()
                render.set_property('editable', True)
                render.connect(
                    'edited', self.__row_edited, model, i + 2, preview)
                col = gtk.TreeViewColumn(header, render, text=i + 2)
                col.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
                view.append_column(col)
            spls = config.get("settings", "splitters")

            for song in songs:
                basename = song("~basename")
                basename = basename.decode(util.fscoding(), "replace")
                row = [song, basename]
                match = pattern.match(song)
                for h in pattern.headers:
                    text = match.get(h, '')
                    if rep: text = text.replace("_", " ")
                    if title: text = util.title(text)
                    if split: text = "\n".join(util.split_value(text, spls))
                    row.append(text)
                model.append(row=row)

            # save for last to potentially save time
            if songs: view.set_model(model)
            preview.set_sensitive(False)
            save.set_sensitive(len(pattern.headers) > 0)

        def __save_files(self, save, parent, view, entry, addreplace):
            pattern_text = entry.get_text().decode('utf-8')
            pattern = util.PatternFromFile(pattern_text)
            add = (addreplace.get_active() == 1)
            config.set("settings", "addreplace", str(addreplace.get_active()))
            win = WritingWindow(parent, len(self.__songs))

            def save_song(model, path, iter):
                song = model[path][0]
                row = model[path]
                changed = False
                if not song.valid() and not qltk.ConfirmAction(
                    parent, _("Tag may not be accurate"),
                    _("<b>%s</b> looks like it was changed while the "
                      "program was running. Saving now without "
                      "refreshing your library might overwrite other "
                      "changes to tag.\n\n"
                      "Save this tag anyway?") % song("~basename")
                    ).run():
                    return True

                for i, h in enumerate(pattern.headers):
                    if row[i + 2]:
                        if not add or h not in song:
                            song[h] = row[i + 2].decode("utf-8")
                            changed = True
                        else:
                            vals = row[i + 2].decode("utf-8")
                            for val in vals.split("\n"):
                                if val not in song.list(h):
                                    song.add(h, val)
                                    changed = True

                if changed:
                    try: song.write()
                    except:
                        qltk.ErrorMessage(
                            parent, _("Unable to edit song"),
                            _("Saving <b>%s</b> failed. The file "
                              "may be read-only, corrupted, or you "
                              "do not have permission to edit it.")%(
                            util.escape(song('~basename')))).run()
                        widgets.watcher.error(song)
                        return True
                    widgets.watcher.changed(song)

                return win.step()
        
            view.get_model().foreach(save_song)
            win.destroy()
            widgets.watcher.refresh()
            save.set_sensitive(False)

        def __row_edited(self, renderer, path, new, model, colnum, preview):
            row = model[path]
            if row[colnum] != new:
                row[colnum] = new
                preview.set_sensitive(True)

        def __preview_tags(self, activator, *args):
            self.__update(self.__songs, *args)

        def __changed(self, activator, preview, save, kw):
            for key, widget in kw.items():
                config.set("settings", key, str(widget.get_active()))
            preview.set_sensitive(True)
            save.set_sensitive(False)

    class RenameFiles(gtk.VBox):
        def __init__(self, prop):
            gtk.VBox.__init__(self, spacing=6)
            self.title = _("Rename Files")
            self.set_border_width(12)

            # ComboEntry and Preview button
            hbox = gtk.HBox(spacing=12)
            combo = qltk.ComboBoxEntrySave(
                const.NBP, const.NBP_EXAMPLES.split("\n"))
            hbox.pack_start(combo)
            preview = qltk.Button(_("_Preview"), gtk.STOCK_CONVERT)
            hbox.pack_start(preview, expand=False)
            self.pack_start(hbox, expand=False)

            # Tree view in a scrolling window
            model = gtk.ListStore(object, str, str)
            view = gtk.TreeView(model)
            column = gtk.TreeViewColumn(
                _('File'), gtk.CellRendererText(), text=1)
            column.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
            view.append_column(column)
            render = gtk.CellRendererText()
            render.set_property('editable', True)
            render.connect('edited', self.__row_edited, model, preview)
            column = gtk.TreeViewColumn(_('New Name'), render, text=2)
            column.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
            view.append_column(column)
            sw = gtk.ScrolledWindow()
            sw.set_shadow_type(gtk.SHADOW_IN)
            sw.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
            sw.add(view)
            self.pack_start(sw)

            # Checkboxes
            replace = gtk.CheckButton(_("Replace spaces with _underscores"))
            replace.set_active(config.state("nbp_space"))
            windows = gtk.CheckButton(_(
                "Replace _Windows-incompatible characters"))
            windows.set_active(config.state("windows"))
            ascii = gtk.CheckButton(_("Replace non-_ASCII characters"))
            ascii.set_active(config.state("ascii"))

            vbox = gtk.VBox()
            vbox.pack_start(replace)
            vbox.pack_start(windows)
            vbox.pack_start(ascii)
            self.pack_start(vbox, expand=False)

            # Save button
            save = gtk.Button(stock=gtk.STOCK_SAVE)
            bbox = gtk.HButtonBox()
            bbox.set_layout(gtk.BUTTONBOX_END)
            bbox.pack_start(save)
            self.pack_start(bbox, expand=False)

            # Set tooltips
            tips = gtk.Tooltips()
            for widget, tip in [
                (windows,
                 _("Characters not allowed in Windows filenames "
                   "(\:?;\"<>|) will be replaced by underscores")),
                (ascii,
                 _("Characters outside of the ASCII set (A-Z, a-z, 0-9, "
                   "and punctuation) will be replaced by underscores"))]:
                tips.set_tip(widget, tip)

            # Connect callbacks
            preview_args = [combo, prop, model, save, preview,
                            replace, windows, ascii]
            preview.connect(
                'clicked', self.__preview_files, *preview_args)
            prop.connect_object(
                'changed', self.__class__.__update, self, *preview_args)

            changed_args = [replace, windows, ascii, save, preview,
                            combo.child]
            for w in [replace, windows, ascii]:
                w.connect_object('toggled', self.__changed, *changed_args)
            combo.child.connect_object(
                'changed', self.__changed, *changed_args)

            save.connect_object(
                'clicked', self.__rename_files, prop, save, model)

        def __changed(self, replace, windows, ascii, save, preview, entry):
            config.set("settings", "windows", str(windows.get_active()))
            config.set("settings", "ascii", str(ascii.get_active()))
            config.set("settings", "nbp_space", str(replace.get_active()))
            save.set_sensitive(False)
            preview.set_sensitive(bool(entry.get_text()))

        def __row_edited(self, renderer, path, new, model, preview):
            row = model[path]
            if row[2] != new:
                row[2] = new
                preview.set_sensitive(True)

        def __preview_files(self, button, *args):
            self.__update(self.__songs, *args)
            save = args[3]
            save.set_sensitive(True)
            preview = args[4]
            preview.set_sensitive(False)

        def __rename_files(self, parent, save, model):
            win = WritingWindow(parent, len(self.__songs))

            def rename(model, path, iter):
                song = model[path][0]
                oldname = model[path][1]
                newname = model[path][2]
                try:
                    newname = newname.encode(util.fscoding(), "replace")
                    if library: library.rename(song, newname)
                    else: song.rename(newname)
                    widgets.watcher.changed(song)
                except:
                    qltk.ErrorMessage(
                        win, _("Unable to rename file"),
                        _("Renaming <b>%s</b> to <b>%s</b> failed. "
                          "Possibly the target file already exists, "
                          "or you do not have permission to make the "
                          "new file or remove the old one.") %(
                        util.escape(oldname), util.escape(newname))).run()
                    widgets.watcher.error(song)
                    return True
                return win.step()
            model.foreach(rename)
            widgets.watcher.refresh()
            save.set_sensitive(False)
            widgets.watcher.refresh()
            win.destroy()

        def __update(self, songs, combo, parent, model, save, preview,
                     replace, windows, ascii):
            self.__songs = songs
            model.clear()
            pattern = combo.child.get_text().decode("utf-8")

            underscore = replace.get_active()
            windows = windows.get_active()
            ascii = ascii.get_active()

            try:
                pattern = util.FileFromPattern(pattern)
            except ValueError: 
                qltk.ErrorMessage(
                    parent,
                    _("Pattern with subdirectories is not absolute"),
                    _("The pattern\n\t<b>%s</b>\ncontains / but "
                      "does not start from root. To avoid misnamed "
                      "directories, root your pattern by starting "
                      "it from the / directory.")%(
                    util.escape(pattern))).run()
                return
            else:
                if combo.child.get_text():
                    combo.prepend_text(combo.child.get_text())
                    combo.write(const.NBP)

            for song in self.__songs:
                newname = pattern.match(song)
                code = util.fscoding()
                newname = newname.encode(code, "replace").decode(code)
                basename = song("~basename").decode(code, "replace")
                if underscore: newname = newname.replace(" ", "_")
                if windows:
                    for c in '\\:*?;"<>|':
                        newname = newname.replace(c, "_")
                if ascii:
                    newname = "".join(
                        map(lambda c: ((ord(c) < 127 and c) or "_"),
                            newname))
                model.append(row=[song, basename, newname])
            preview.set_sensitive(False)
            save.set_sensitive(bool(combo.child.get_text()))

    class TrackNumbers(gtk.VBox):
        def __init__(self, prop):
            gtk.VBox.__init__(self, spacing=6)
            self.title = _("Track Numbers")
            self.set_border_width(12)
            hbox = gtk.HBox(spacing=18)
            hbox2 = gtk.HBox(spacing=12)

            hbox_start = gtk.HBox(spacing=3)
            label_start = gtk.Label(_("Start fro_m:"))
            label_start.set_use_underline(True)
            spin_start = gtk.SpinButton()
            spin_start.set_range(1, 99)
            spin_start.set_increments(1, 10)
            spin_start.set_value(1)
            label_start.set_mnemonic_widget(spin_start)
            hbox_start.pack_start(label_start)
            hbox_start.pack_start(spin_start)

            hbox_total = gtk.HBox(spacing=3)
            label_total = gtk.Label(_("_Total tracks:"))
            label_total.set_use_underline(True)
            spin_total = gtk.SpinButton()
            spin_total.set_range(0, 99)
            spin_total.set_increments(1, 10)
            label_total.set_mnemonic_widget(spin_total)
            hbox_total.pack_start(label_total)
            hbox_total.pack_start(spin_total)
            preview = qltk.Button(_("_Preview"), gtk.STOCK_CONVERT)

            hbox2.pack_start(hbox_start, expand=True, fill=False)
            hbox2.pack_start(hbox_total, expand=True, fill=False)
            hbox2.pack_start(preview, expand=True, fill=False)

            model = gtk.ListStore(object, str, str)
            view = HintedTreeView(model)

            self.pack_start(hbox2, expand=False)

            column = gtk.TreeViewColumn(
                _('File'), gtk.CellRendererText(), text=1)
            column.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
            view.append_column(column)
            column = gtk.TreeViewColumn(
                _('Track'), gtk.CellRendererText(), text=2)
            column.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
            view.append_column(column)
            view.set_reorderable(True)
            w = gtk.ScrolledWindow()
            w.set_shadow_type(gtk.SHADOW_IN)
            w.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
            w.add(view)
            self.pack_start(w)

            bbox = gtk.HButtonBox()
            bbox.set_spacing(12)
            bbox.set_layout(gtk.BUTTONBOX_END)
            save = gtk.Button(stock=gtk.STOCK_SAVE)
            save.connect_object(
                'clicked', self.__save_files, prop, model)
            revert = gtk.Button(stock=gtk.STOCK_REVERT_TO_SAVED)
            revert.connect_object(
                'clicked', self.__revert_files, spin_total, model,
                save, revert)
            bbox.pack_start(revert)
            bbox.pack_start(save)
            self.pack_start(bbox, expand=False)

            preview_args = [spin_start, spin_total, model, save, revert]
            preview.connect('clicked', self.__preview_tracks, *preview_args)
            spin_total.connect(
                'value-changed', self.__preview_tracks, *preview_args)
            spin_start.connect(
                'value-changed', self.__preview_tracks, *preview_args)
            view.connect_object(
                'drag-end', self.__class__.__preview_tracks, self,
                *preview_args)

            prop.connect_object(
                'changed', self.__class__.__update, self,
                spin_total, model, save, revert)

        def __save_files(self, parent, model):
            win = WritingWindow(parent, len(self.__songs))
            def settrack(model, path, iter):
                song = model[iter][0]
                track = model[iter][2]
                if song.get("tracknumber") == track: return win.step()
                if not song.valid() and not qltk.ConfirmAction(
                    win, _("Tag may not be accurate"),
                    _("<b>%s</b> looks like it was changed while the "
                      "program was running. Saving now without "
                      "refreshing your library might overwrite other "
                      "changes to tag.\n\n"
                      "Save this tag anyway?") % song("~basename")
                    ).run():
                    return True
                song["tracknumber"] = track
                try: song.write()
                except:
                    qltk.ErrorMessage(
                        win, _("Unable to edit song"),
                        _("Saving <b>%s</b> failed. The file may be "
                          "read-only, corrupted, or you do not have "
                          "permission to edit it.")%(
                        util.escape(song('~basename')))).run()
                    widgets.watcher.error(song)
                    return True
                widgets.watcher.changed(song)
                return win.step()
            model.foreach(settrack)
            widgets.watcher.refresh()
            win.destroy()

        def __revert_files(self, *args):
            self.__update(self.__songs, *args)

        def __preview_tracks(self, ctx, start, total, model, save, revert):
            start = start.get_value_as_int()
            total = total.get_value_as_int()
            def refill(model, path, iter):
                if total: s = "%d/%d" % (path[0] + start, total)
                else: s = str(path[0] + start)
                model[iter][2] = s
            model.foreach(refill)
            save.set_sensitive(True)
            revert.set_sensitive(True)

        def __update(self, songs, total, model, save, revert):
            songs = songs[:]; songs.sort()
            self.__songs = songs
            model.clear()
            total.set_value(len(songs))
            for song in songs:
                if not song.can_change("tracknumber"):
                    self.set_sensitive(False)
                    break
            else: self.set_sensitive(True)
            for song in songs:
                basename = util.fsdecode(song("~basename"))
                model.append(row=[song, basename, song("tracknumber")])
            save.set_sensitive(False)
            revert.set_sensitive(False)

    def __init__(self, songs):
        gtk.Window.__init__(self)
        self.set_default_size(300, 430)
        self.set_type_hint(gtk.gdk.WINDOW_TYPE_HINT_DIALOG)
        icon_theme = gtk.icon_theme_get_default()
        self.set_icon(icon_theme.load_icon(
            const.ICON, 64, gtk.ICON_LOOKUP_USE_BUILTIN))
        notebook = qltk.Notebook()
        pages = [self.Information(self, library=True)]
        pages.extend([Ctr(self) for Ctr in
                      [self.EditTags, self.TagByFilename, self.RenameFiles]])
        if len(songs) > 1:
            pages.append(self.TrackNumbers(self))
        for page in pages: notebook.append_page(page)
        self.set_border_width(12)
        vbox = gtk.VBox(spacing=12)
        vbox.pack_start(notebook)

        fbasemodel = gtk.ListStore(object, str, str, str)
        fmodel = gtk.TreeModelSort(fbasemodel)
        fview = HintedTreeView(fmodel)
        selection = fview.get_selection()
        selection.set_mode(gtk.SELECTION_MULTIPLE)
        selection.connect('changed', self.__selection_changed)

        if len(songs) > 1:
            expander = gtk.Expander(_("Apply to these _files..."))
            c1 = gtk.TreeViewColumn(_('File'), gtk.CellRendererText(), text=1)
            c1.set_sort_column_id(1)
            c1.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
            c2 = gtk.TreeViewColumn(_('Path'), gtk.CellRendererText(), text=2)
            c2.set_sort_column_id(3)
            c2.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
            fview.append_column(c1)
            fview.append_column(c2)
            fview.set_size_request(-1, 130)
            sw = gtk.ScrolledWindow()
            sw.add(fview)
            sw.set_shadow_type(gtk.SHADOW_IN)
            sw.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
            expander.add(sw)
            expander.set_use_underline(True)
            vbox.pack_start(expander, expand=False)

        bbox = gtk.HButtonBox()
        bbox.set_layout(gtk.BUTTONBOX_END)
        button = gtk.Button(stock=gtk.STOCK_CLOSE)
        button.connect_object('clicked', gtk.Window.destroy, self)
        bbox.pack_start(button)
        vbox.pack_start(bbox, expand=False)

        for song in songs:
            fbasemodel.append(
                row = [song,
                       util.fsdecode(song("~basename")),
                       util.fsdecode(song("~dirname")),
                       song["~filename"]])

        self.connect_object('changed', SongProperties.__set_title, self)

        selection.select_all()
        self.add(vbox)
        self.connect_object('destroy', fview.set_model, None)
        self.connect_object('destroy', gtk.ListStore.clear, fbasemodel)
        self.connect_object(
            'destroy', gtk.TreeModelSort.clear_cache, fmodel)

        s1 = widgets.watcher.connect_object(
            'refresh', SongProperties.__refill, self, fbasemodel)
        s2 = widgets.watcher.connect_object(
            'removed', SongProperties.__remove, self, fbasemodel, selection)
        s3 = widgets.watcher.connect_object(
            'refresh', selection.emit, 'changed')
        self.connect_object('destroy', widgets.watcher.disconnect, s1)
        self.connect_object('destroy', widgets.watcher.disconnect, s2)
        self.connect_object('destroy', widgets.watcher.disconnect, s3)

        self.emit('changed', songs)
        self.show_all()

    def __remove(self, song, model, selection):
        to_remove = [None]
        def remove(model, path, iter):
            if model[iter][0] == song: to_remove.append(iter)
            return bool(to_remove[-1])
        model.foreach(remove)
        if to_remove[-1]:
            model.remove(to_remove[-1])
            self.__refill(model)
            selection.emit('changed')

    def __set_title(self, songs):
        if songs:
            if len(songs) == 1: title = songs[0].comma("title")
            else: title = _("%s and %d more") % (
                songs[0].comma("title"), len(songs) - 1)
            self.set_title("%s - %s" % (title, _("Properties")))
        else: self.set_title(_("Properties"))

    def __refill(self, model):
        def refresh(model, iter, path):
            song = model[iter][0]
            model[iter][1] = song("~basename")
            model[iter][2] = song("~dirname")
            model[iter][3] = song["~filename"]
        model.foreach(refresh)

    def __selection_changed(self, selection):
        model = selection.get_tree_view().get_model()
        if model and len(model) == 1: self.emit('changed', [model[(0,)][0]])
        else:
            songs = []
            def get_songs(model, path, iter, songs):
                songs.append(model[iter][0])
            selection.selected_foreach(get_songs, songs)
            self.emit('changed', songs)

gobject.type_register(SongProperties)

class DirectoryTree(gtk.TreeView):
    def cell_data(column, cell, model, iter):
        cell.set_property('text', util.fsdecode(
            os.path.basename(model[iter][0])) or "/")
    cell_data = staticmethod(cell_data)

    def __init__(self, initial=None):
        gtk.TreeView.__init__(self, gtk.TreeStore(str))
        column = gtk.TreeViewColumn(_("Folders"))
        column.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
        render = gtk.CellRendererPixbuf()
        render.set_property('stock_id', gtk.STOCK_DIRECTORY)
        column.pack_start(render, expand=False)
        render = gtk.CellRendererText()
        column.pack_start(render)
        column.set_cell_data_func(render, self.cell_data)

        column.set_attributes(render, text=0)
        self.append_column(column)
        folders = [os.environ["HOME"], "/"]
        # Read in the GTK bookmarks list; gjc says this is the right way
        try: f = file(os.path.join(os.environ["HOME"], ".gtk-bookmarks"))
        except: pass
        else:
            import urlparse
            for line in f.readlines():
                folders.append(urlparse.urlsplit(line.rstrip())[2])
            folders = filter(os.path.isdir, folders)

        for path in folders:
            niter = self.get_model().append(None, [path])
            self.get_model().append(niter, ["dummy"])
        self.get_selection().set_mode(gtk.SELECTION_MULTIPLE)
        self.connect('test-expand-row', DirectoryTree.__expanded,
                     self.get_model())

        if initial:
            path = []
            head, tail = os.path.split(initial)
            while head != os.path.dirname(os.environ["HOME"]) and tail != '':
                if tail:
                    dirs = [d for d in dircache.listdir(head) if
                            (d[0] != "." and
                             os.path.isdir(os.path.join(head,d)))]
                    try: path.insert(0, dirs.index(tail))
                    except ValueError: break
                head, tail = os.path.split(head)

            if initial.startswith(os.environ["HOME"]): path.insert(0, 0)
            else: path.insert(0, 1)
            for i in range(len(path)):
                self.expand_row(tuple(path[:i+1]), False)
            self.get_selection().select_path(tuple(path))
            self.scroll_to_cell(tuple(path))

        else: pass

        menu = gtk.Menu()
        m = gtk.ImageMenuItem(_("New Folder..."))
        m.get_image().set_from_stock(gtk.STOCK_NEW, gtk.ICON_SIZE_MENU)
        m.connect('activate', self.__mkdir)
        menu.append(m)
        m = gtk.ImageMenuItem(gtk.STOCK_DELETE)
        m.connect('activate', self.__rmdir)
        menu.append(m)
        menu.show_all()
        self.connect('button-press-event', DirectoryTree.__button_press, menu)

    def __button_press(self, event, menu):
        if event.button != 3: return False
        x, y = map(int, [event.x, event.y])
        try: path, col, cellx, celly = self.get_path_at_pos(x, y)
        except TypeError: return True
        directory = self.get_model()[path][0]
        menu.get_children()[1].set_sensitive(len(os.listdir(directory)) == 0)
        selection = self.get_selection()
        selection.unselect_all()
        selection.select_path(path)
        menu.popup(None, None, None, event.button, event.time)

    def __mkdir(self, button):
        model, rows = self.get_selection().get_selected_rows()
        if len(rows) != 1: return

        row = rows[0]
        directory = model[row][0]
        uparent = util.unexpand(directory)
        dir = GetStringDialog(
            None, _("New folder"), _("Enter a name for the new folder:"),
            ).run()

        if dir:
            dir = util.fsencode(dir.decode('utf-8'))
            fullpath = os.path.realpath(os.path.join(directory, dir))
            try: os.makedirs(fullpath)
            except EnvironmentError, err:
                error = "<b>%s</b>: %s" % (err.filename, err.strerror)
                qltk.ErrorMessage(
                    None, _("Unable to create folder"), error).run()
            else:
                self.emit('test-expand-row', model.get_iter(row), row)
                self.expand_row(row, False)

    def __rmdir(self, button):
        model, rows = self.get_selection().get_selected_rows()
        if len(rows) != 1: return
        directory = model[rows[0]][0]
        try: os.rmdir(directory)
        except EnvironmentError, err:
            error = "<b>%s</b>: %s" % (err.filename, err.strerror)
            qltk.ErrorMessage(
                None, _("Unable to delete folder"), error).run()
        else:
            prow = rows[0][:-1]
            expanded = self.row_expanded(prow)
            self.emit('test-expand-row', model.get_iter(prow), prow)
            if expanded: self.expand_row(prow, False)

    def __expanded(self, iter, path, model):
        if model is None: return
        while model.iter_has_child(iter):
            model.remove(model.iter_children(iter))
        dir = model[iter][0]
        for base in dircache.listdir(dir):
            path = os.path.join(dir, base)
            if (base[0] != "." and os.access(path, os.R_OK) and
                os.path.isdir(path)):
                niter = model.append(iter, [path])
                if filter(os.path.isdir,
                          [os.path.join(path, d) for d in
                           dircache.listdir(path) if d[0] != "."]):
                    model.append(niter, ["dummy"])
        if not model.iter_has_child(iter): return True

class FileSelector(gtk.VPaned):
    def cell_data(column, cell, model, iter):
        cell.set_property(
            'text', util.fsdecode(os.path.basename(model[iter][0])))
    cell_data = staticmethod(cell_data)

    __gsignals__ = { 'changed': (gobject.SIGNAL_RUN_LAST,
                                 gobject.TYPE_NONE, (gtk.TreeSelection,))
                     }

    def __init__(self, initial=None, filter=formats.filter):
        gtk.VPaned.__init__(self)
        self.__filter = filter

        dirlist = DirectoryTree(initial)
        filelist = HintedTreeView(gtk.ListStore(str))
        column = gtk.TreeViewColumn(_("Audio files"))
        column.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
        render = gtk.CellRendererPixbuf()
        render.set_property('stock_id', gtk.STOCK_FILE)
        column.pack_start(render, expand=False)
        render = gtk.CellRendererText()
        column.pack_start(render)
        column.set_cell_data_func(render, self.cell_data)
        column.set_attributes(render, text=0)
        filelist.append_column(column)
        filelist.set_rules_hint(True)
        filelist.get_selection().set_mode(gtk.SELECTION_MULTIPLE)

        self.__sig = filelist.get_selection().connect(
            'changed', self.__changed)

        dirlist.get_selection().connect(
            'changed', self.__fill, filelist)
        dirlist.get_selection().emit('changed')
        def select_all_files(view, path, col, fileselection):
            view.expand_row(path, False)
            fileselection.select_all()
        dirlist.connect('row-activated', select_all_files,
            filelist.get_selection())

        sw = gtk.ScrolledWindow()
        sw.add(dirlist)
        sw.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        sw.set_shadow_type(gtk.SHADOW_IN)
        self.pack1(sw, resize=True)

        sw = gtk.ScrolledWindow()
        sw.add(filelist)
        sw.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        sw.set_shadow_type(gtk.SHADOW_IN)
        self.pack2(sw, resize=True)

    def rescan(self):
        self.get_child1().child.get_selection().emit('changed')

    def __changed(self, selection):
        self.emit('changed', selection)

    def __fill(self, selection, filelist):
        fselect = filelist.get_selection()
        fselect.handler_block(self.__sig)
        fmodel, frows = fselect.get_selected_rows()
        selected = [fmodel[row][0] for row in frows]
        fmodel = filelist.get_model()
        fmodel.clear()
        dmodel, rows = selection.get_selected_rows()
        dirs = [dmodel[row][0] for row in rows]
        files = []
        for dir in dirs:
            for file in filter(self.__filter, dircache.listdir(dir)):
                fmodel.append([os.path.join(dir, file)])
        def select_paths(model, path, iter, selection):
            if model[path][0] in selected:
                selection.select_path(path)
        if fmodel: fmodel.foreach(select_paths, fselect)
        fselect.handler_unblock(self.__sig)
        fselect.emit('changed')

gobject.type_register(FileSelector)

class ExFalsoWindow(gtk.Window):
    __gsignals__ = { 'changed': (gobject.SIGNAL_RUN_LAST,
                                 gobject.TYPE_NONE, (object,))
                     }

    def __init__(self, dir=None):
        gtk.Window.__init__(self)
        self.set_title("Ex Falso")
        icon_theme = gtk.icon_theme_get_default()
        p = gtk.gdk.pixbuf_new_from_file("exfalso.png")
        gtk.icon_theme_add_builtin_icon(const.ICON, 64, p)
        self.set_icon(icon_theme.load_icon(
            const.ICON, 64, gtk.ICON_LOOKUP_USE_BUILTIN))
        self.set_border_width(12)
        self.set_default_size(700, 500)
        self.add(gtk.HPaned())
        fs = FileSelector(dir)
        self.child.pack1(fs, resize=True)
        nb = qltk.Notebook()
        nb.append_page(SongProperties.Information(self, library=False))
        for Page in [SongProperties.EditTags,
                     SongProperties.TagByFilename,
                     SongProperties.RenameFiles,
                     SongProperties.TrackNumbers]:
            nb.append_page(Page(self))
        self.child.pack2(nb, resize=False, shrink=False)
        fs.connect('changed', self.__changed, nb)
        self.__cache = {}
        s = widgets.watcher.connect_object('refresh', FileSelector.rescan, fs)
        self.connect_object('destroy', widgets.watcher.disconnect, s)
        self.connect('destroy', gtk.main_quit)
        self.emit('changed', [])

    def __changed(self, selector, selection, notebook):
        model, rows = selection.get_selected_rows()
        files = []
        for row in rows:
            filename = model[row][0]
            if not os.path.exists(filename): pass
            elif filename in self.__cache: files.append(self.__cache[filename])
            else: files.append(formats.MusicFile(model[row][0]))
        try:
            while True: files.remove(None)
        except ValueError: pass
        self.emit('changed', files)
        self.__cache.clear()
        if len(files) == 0: self.set_title("Ex Falso")
        elif len(files) == 1:
            self.set_title("%s - Ex Falso" % files[0]("title"))
        else:
            self.set_title(
                "%s - Ex Falso" %
                (_("%s and %d more") % (files[0]("title"), len(files) - 1)))
        self.__cache = dict([(song["~filename"], song) for song in files])

gobject.type_register(ExFalsoWindow)

class WritingWindow(qltk.WaitLoadWindow):
    def __init__(self, parent, count):
        qltk.WaitLoadWindow.__init__(self, parent, count,
                                _("Saving the songs you changed.\n\n"
                                  "%d/%d songs saved"), (0, count))

    def step(self):
        return qltk.WaitLoadWindow.step(self, self.current + 1, self.count)

# Return a 'natural' version of the tag for human-readable bits.
# Strips ~ and ~# from the start and runs it through a map (which
# the user can configure).
def tag(name, cap=True):
    try:
        if name[0] == "~":
            if name[1] == "#": name = name[2:]
            else: name = name[1:]
        parts = [_(HEADERS_FILTER.get(n, n)) for n in name.split("~")]
        if cap: parts = map(util.capitalize, parts)
        return " / ".join(parts)
    except IndexError:
        return _("Invalid tag name")

HEADERS_FILTER = { "tracknumber": "track",
                   "discnumber": "disc",
                   "lastplayed": "last played",
                   "filename": "full name",
                   "playcount": "play count",
                   "skipcount": "skip count",
                   "mtime": "modified",
                   "basename": "filename",
                   "dirname": "directory"}

def website_wrap(activator, link):
    if not util.website(link):
        qltk.ErrorMessage(
            widgets.main, _("Unable to start a web browser"),
            _("A web browser could not be found. Please set "
              "your $BROWSER variable, or make sure "
              "/usr/bin/sensible-browser exists.")).run()

def init():
    if config.get("settings", "headers").split() == []:
       config.set("settings", "headers", "title")
    for opt in config.options("header_maps"):
        val = config.get("header_maps", opt)
        HEADERS_FILTER[opt] = val

    gtk.about_dialog_set_url_hook(website_wrap)
    watcher = widgets.watcher = SongWatcher()
    FSInterface(watcher)
    widgets.main = MainWindow()
    player.playlist.info = widgets.watcher

    # If the OSD module is not available, no signal is registered and
    # the reference is dropped. If it is available, a reference to it is
    # stored in its signal registered with SongWatcher.
    Osd(watcher)

    util.mkdir(const.DIR)
    return widgets.main

def save_library(thread):
    player.playlist.quitting()
    thread.join()
    print to(_("Saving song library."))
    try: library.save(const.LIBRARY)
    except EnvironmentError, err:
        print err

    try: config.write(const.CONFIG)
    except EnvironmentError, err:
        print err

    for fn in [const.PAUSED, const.CURRENT, const.CONTROL]:
        try: os.unlink(fn)
        except EnvironmentError: pass

def error_and_quit():
    qltk.ErrorMessage(None,
                      _("No audio device found"),
                      _("Quod Libet was unable to open your audio device. "
                        "Often this means another program is using it, or "
                        "your audio drivers are not configured.\n\nQuod Libet "
                        "will now exit.")).run()
    gtk.main_quit()
