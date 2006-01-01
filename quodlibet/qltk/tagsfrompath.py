# -*- coding: utf-8 -*-
# Copyright 2004-2005 Joe Wreschnig, Michael Urman, Iñigo Serna
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation
#
# $Id$

import os
import sre
import gtk, gobject

import stock
import qltk
from qltk.wlw import WritingWindow
from qltk.cbes import ComboBoxEntrySave
from qltk.ccb import ConfigCheckButton

import const
import config
import util

import __builtin__; __builtin__.__dict__.setdefault("_", lambda a: a)

class TagsFromPattern(object):
    def __init__(self, pattern):
        self.compile(pattern)

    def compile(self, pattern):
        self.headers = []
        self.slashes = len(pattern) - len(pattern.replace(os.path.sep,'')) + 1
        self.pattern = None
        # patterns look like <tagname> non regexy stuff <tagname> ...
        pieces = sre.split(r'(<[A-Za-z0-9_]+>)', pattern)
        override = { '<tracknumber>': r'\d\d?', '<discnumber>': r'\d\d??' }
        for i, piece in enumerate(pieces):
            if not piece: continue
            if piece[0]+piece[-1] == '<>' and piece[1:-1].isalnum():
                piece = piece.lower()   # canonicalize to lowercase tag names
                pieces[i] = '(?P%s%s)' % (piece, override.get(piece, '.+'))
                self.headers.append(piece[1:-1].encode("ascii", "replace"))
            else:
                pieces[i] = sre.escape(piece)

        # some slight magic to anchor searches "nicely"
        # nicely means if it starts with a <tag>, anchor with a /
        # if it ends with a <tag>, anchor with .xxx$
        # but if it's a <tagnumber>, don't bother as \d+ is sufficient
        # and if it's not a tag, trust the user
        if pattern.startswith('<') and not pattern.startswith('<tracknumber>')\
                and not pattern.startswith('<discnumber>'):
            pieces.insert(0, os.path.sep)
        if pattern.endswith('>') and not pattern.endswith('<tracknumber>')\
                and not pattern.endswith('<discnumber>'):
            pieces.append(r'(?:\.\w+)$')

        self.pattern = sre.compile(''.join(pieces))

    def match(self, song):
        if isinstance(song, dict):
            song = song['~filename'].decode(util.fscoding, "replace")
        # only match on the last n pieces of a filename, dictated by pattern
        # this means no pattern may effectively cross a /, despite .* doing so
        sep = os.path.sep
        matchon = sep+sep.join(song.split(sep)[-self.slashes:])
        match = self.pattern.search(matchon)

        # dicts for all!
        if match is None: return {}
        else: return match.groupdict()

class FilterCheckButton(ConfigCheckButton):
    __gsignals__ = {
        "changed": (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ())
        }

    def __init__(self):
        super(FilterCheckButton, self).__init__(
            self._label, "tagsfrompath", self._key)
        try: self.set_active(config.getboolean("tagsfrompath", self._key))
        except: pass
        self.connect_object('toggled', self.emit, 'changed')
    active = property(lambda s: s.get_active())

    def filter(self, tag, value): raise NotImplementedError
gobject.type_register(FilterCheckButton)

class UnderscoresToSpaces(FilterCheckButton):
    _label = _("Replace _underscores with spaces")
    _key = "underscores"
    _order = 1.0

    def filter(self, tag, value): return value.replace("_", " ")

class TitleCase(FilterCheckButton):
    _label = _("_Title-case tags")
    _key = "titlecase"
    _order = 1.1
    def filter(self, tag, value): return util.title(value)

class SplitTag(FilterCheckButton):
    _label = _("Split into multiple _values")
    _key = "split"
    _order = 1.2
    def filter(self, tag, value):
        spls = config.get("editing", "split_on").decode('utf-8', 'replace')
        spls = spls.split()
        return "\n".join(util.split_value(value, spls))

class TagsFromPath(gtk.VBox):
    FILTERS = [UnderscoresToSpaces, TitleCase, SplitTag]

    def __init__(self, parent, watcher):
        gtk.VBox.__init__(self, spacing=6)
        self.title = _("Tags From Path")
        self.set_border_width(12)
        hbox = gtk.HBox(spacing=12)

        # Main buttons
        self.preview = gtk.Button(stock=stock.PREVIEW)
        self.save = gtk.Button(stock=gtk.STOCK_SAVE)

        # Text entry and preview button
        combo = ComboBoxEntrySave(
            const.TBP, const.TBP_EXAMPLES.split("\n"))
        hbox.pack_start(combo)
        self.entry = combo.child
        self.entry.connect('changed', self.__changed)

        hbox.pack_start(self.preview, expand=False)
        self.pack_start(hbox, expand=False)

        # Header preview display
        self.view = view = gtk.TreeView()
        sw = gtk.ScrolledWindow()
        sw.set_shadow_type(gtk.SHADOW_IN)
        sw.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        sw.add(view)
        self.pack_start(sw)

        # Options
        vbox = gtk.VBox()
        addreplace = gtk.combo_box_new_text()
        addreplace.append_text(_("Tags replace existing ones"))
        addreplace.append_text(_("Tags are added to existing ones"))
        addreplace.set_active(config.getboolean("tagsfrompath", "add"))
        addreplace.connect('changed', self.__add_changed)
        vbox.pack_start(addreplace)
        filters = [Kind() for Kind in self.FILTERS]
        filters.sort()
        map(vbox.pack_start, filters)
        self.pack_start(vbox, expand=False)

        hb = gtk.HBox()
        expander = gtk.Expander(label=_("_More options..."))
        expander.set_use_underline(True)
        adj = gtk.Alignment(yalign=1.0, xscale=1.0)
        adj.add(expander)
        hb.pack_start(adj)
        bbox = gtk.HButtonBox()
        bbox.set_layout(gtk.BUTTONBOX_END)
        bbox.pack_start(self.save)
        hb.pack_start(bbox, expand=False)
        self.pack_start(hb, expand=False)

        for f in filters:
            f.connect_object('changed', self.__preview, None, combo)

        vbox = gtk.VBox()

        self.__filters = []
        plugins = parent.plugins.TagsFromPathPlugins()
        
        for Kind in plugins:
            try: f = Kind()
            except:
                import traceback
                traceback.print_exc()
                continue
                
            try: vbox.pack_start(f)
            except:
                import traceback
                traceback.print_exc()
            else:
                try: f.connect_object('changed', self.__preview, None, combo)
                except:
                    try: f.connect_object(
                        'preview', self.__changed, combo.child)
                    except:
                        import traceback
                        traceback.print_exc()
                    else: self.__filters.append(f)
                else: self.__filters.append(f)

        # Custom filters run before the premade ones.
        self.__filters.extend(filters)
        self.__filters.sort()

        sw = gtk.ScrolledWindow()
        sw.set_shadow_type(gtk.SHADOW_IN)
        sw.set_policy(gtk.POLICY_NEVER, gtk.POLICY_AUTOMATIC)
        sw.add_with_viewport(vbox)
        self.pack_start(sw, expand=False)

        expander.connect("notify::expanded", self.__notify_expanded, sw)
        expander.set_expanded(False)

        self.preview.connect_object('clicked', self.__preview, None, combo)
        parent.connect_object('changed', self.__class__.__preview, self, combo)

        # Save changes
        self.save.connect_object('clicked', self.__save, addreplace, watcher)

        self.show_all()
        # Don't display the expander if there aren't any plugins.
        if len(self.__filters) == len(self.FILTERS): expander.hide()
        sw.hide()

    def __changed(self, entry):
        self.save.set_sensitive(False)
        self.preview.set_sensitive(bool(entry.get_text()))

    def __notify_expanded(self, expander, event, vbox):
        vbox.set_property('visible', expander.get_property('expanded'))

    def __add_changed(self, combo):
        config.set("tagsfrompath", "add", str(bool(combo.get_active())))

    def __preview(self, songs, combo):
        from library import AudioFileGroup
        if songs is None: songs = self.__songs
        else: self.__songs = songs

        songinfo = AudioFileGroup(songs)
        if songs: pattern_text = self.entry.get_text().decode("utf-8")
        else: pattern_text = ""
        try: pattern = TagsFromPattern(pattern_text)
        except sre.error:
            qltk.ErrorMessage(
                self, _("Invalid pattern"),
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
            qltk.ErrorMessage(
                self, title, msg % ", ".join(invalid)).run()
            pattern = TagsFromPattern("")

        self.view.set_model(None)
        model = gtk.ListStore(
            object, str, *([str] * len(pattern.headers)))
        for col in self.view.get_columns():
            self.view.remove_column(col)

        col = gtk.TreeViewColumn(_('File'), gtk.CellRendererText(),
                                 text=1)
        col.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
        self.view.append_column(col)
        for i, header in enumerate(pattern.headers):
            render = gtk.CellRendererText()
            render.set_property('editable', True)
            render.connect('edited', self.__row_edited, model, i + 2)
            col = gtk.TreeViewColumn(header, render, text=i + 2)
            col.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
            self.view.append_column(col)

        for song in songs:
            basename = song("~basename")
            basename = basename.decode(util.fscoding, "replace")
            row = [song, basename]
            match = pattern.match(song)
            for h in pattern.headers:
                text = match.get(h, '')
                for f in self.__filters:
                    if f.active: text = f.filter(h, text)
                row.append(text)
            model.append(row=row)

        # save for last to potentially save time
        if songs: self.view.set_model(model)
        self.preview.set_sensitive(False)
        self.save.set_sensitive(len(pattern.headers) > 0)

    def __save(self, addreplace, watcher):
        pattern_text = self.entry.get_text().decode('utf-8')
        pattern = TagsFromPattern(pattern_text)
        add = bool(addreplace.get_active())
        win = WritingWindow(self, len(self.__songs))

        was_changed = []

        for row in self.view.get_model():
            song = row[0]
            changed = False
            if not song.valid() and not qltk.ConfirmAction(
                self, _("Tag may not be accurate"),
                _("<b>%s</b> changed while the program was running. "
                  "Saving without refreshing your library may "
                  "overwrite other changes to the song.\n\n"
                  "Save this song anyway?") %(
                util.escape(util.fsdecode(song("~basename"))))
                ).run():
                break

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
                        self, _("Unable to edit song"),
                        _("Saving <b>%s</b> failed. The file "
                          "may be read-only, corrupted, or you "
                          "do not have permission to edit it.")%(
                        util.escape(util.fsdecode(song('~basename'))))
                        ).run()
                    watcher.reload(song)
                    break
                was_changed.append(song)

            if win.step(): break

        win.destroy()
        watcher.changed(was_changed)
        watcher.refresh()
        self.save.set_sensitive(False)

    def __row_edited(self, renderer, path, new, model, colnum):
        row = model[path]
        if row[colnum] != new:
            row[colnum] = new
            self.preview.set_sensitive(True)
