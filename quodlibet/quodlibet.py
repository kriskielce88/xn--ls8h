#!/usr/bin/env python2
# -*- coding: utf-8 -*-

# Copyright 2004-2005 Joe Wreschnig, Michael Urman, Iñigo Serna
#           2012,2013 Christoph Reiter
#           2010-2014 Nick Boultbee
# <quod-libet-development@googlegroups.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

import sys

import os

from quodlibet.cli import process_arguments, control
from quodlibet.util.dprint import print_d


def main():
    try:
        # we want basic commands not to import gtk (doubles process time)
        assert "gi.repository.Gtk" not in sys.modules
        sys.modules["gi.repository.Gtk"] = None
        startup_actions = process_arguments()

        # this will exit if it succeeds
        control('focus', ignore_error=True)

    finally:
        sys.modules.pop("gi.repository.Gtk", None)

    import quodlibet
    from quodlibet import app
    from quodlibet.qltk import add_signal_watch, icons
    add_signal_watch(app.quit)

    import quodlibet.player
    import quodlibet.library
    from quodlibet import config
    from quodlibet import browsers
    from quodlibet import const
    from quodlibet import util

    config.init(const.CONFIG)

    app.name = "Quod Libet"
    app.id = "quodlibet"

    quodlibet.init(icon=icons.QUODLIBET, name=app.name, proc_title=app.id)

    print_d("Initializing main library (%s)" % (
            quodlibet.util.path.unexpand(const.LIBRARY)))

    library = quodlibet.library.init(const.LIBRARY)
    app.library = library

    from quodlibet.player import PlayerError
    # this assumes that nullbe will always succeed
    for backend in [config.get("player", "backend"), "nullbe"]:
        try:
            player = quodlibet.init_backend(backend, app.librarian)
        except PlayerError as error:
            print_e("%s. %s" % (error.short_desc, error.long_desc))
        else:
            break
    app.player = player

    os.environ["PULSE_PROP_media.role"] = "music"
    os.environ["PULSE_PROP_application.icon_name"] = "quodlibet"

    browsers.init()

    from quodlibet.qltk.songlist import SongList, get_columns

    from quodlibet.util.collection import Album
    try:
        cover_size = config.getint("browsers", "cover_size")
    except config.Error:
        pass
    else:
        if cover_size > 0:
            Album.COVER_SIZE = cover_size

    headers = get_columns()
    SongList.set_all_column_headers(headers)

    for opt in config.options("header_maps"):
        val = config.get("header_maps", opt)
        util.tags.add(opt, val)

    in_all = ("~filename ~uri ~#lastplayed ~#rating ~#playcount ~#skipcount "
              "~#added ~#bitrate ~current ~#laststarted ~basename "
              "~dirname").split()
    for Kind in browsers.browsers:
        if Kind.headers is not None:
            Kind.headers.extend(in_all)
        Kind.init(library)

    pm = quodlibet.init_plugins("no-plugins" in startup_actions)

    if hasattr(player, "init_plugins"):
        player.init_plugins()

    from quodlibet.qltk import unity
    unity.init("quodlibet.desktop", player)

    from quodlibet.qltk.songsmenu import SongsMenu
    SongsMenu.init_plugins()

    from quodlibet.util.cover import CoverManager
    app.cover_manager = CoverManager()
    app.cover_manager.init_plugins()

    from quodlibet.plugins.playlist import PLAYLIST_HANDLER
    PLAYLIST_HANDLER.init_plugins()

    from quodlibet.qltk.quodlibetwindow import QuodLibetWindow
    app.window = window = QuodLibetWindow(library, player)

    from quodlibet.plugins.events import EventPluginHandler
    pm.register_handler(EventPluginHandler(library.librarian, player))

    from quodlibet.mmkeys import MMKeysHandler
    from quodlibet.remote import Remote
    from quodlibet.commands import registry as cmd_registry
    from quodlibet.qltk.tracker import SongTracker, FSInterface
    try:
        from quodlibet.qltk.dbus_ import DBusHandler
    except ImportError:
        DBusHandler = lambda player, library: None

    mmkeys_handler = MMKeysHandler(app.name, window, player)
    if "QUODLIBET_NO_MMKEYS" not in os.environ:
        mmkeys_handler.start()
    fsiface = FSInterface(player)
    remote = Remote(app, cmd_registry)
    remote.start()

    DBusHandler(player, library)
    tracker = SongTracker(library.librarian, player, window.playlist)

    from quodlibet.qltk import session
    session.init("quodlibet")

    quodlibet.enable_periodic_save(save_library=True)

    if "start-playing" in startup_actions:
        player.paused = False

    # restore browser windows
    from quodlibet.qltk.browser import LibraryBrowser
    from gi.repository import GLib
    GLib.idle_add(LibraryBrowser.restore, library, player,
                  priority=GLib.PRIORITY_HIGH)

    def before_quit():
        print_d("Saving active browser state")
        try:
            app.browser.save()
        except NotImplementedError:
            pass

    quodlibet.main(window, before_quit=before_quit)

    quodlibet.finish_first_session(app.id)
    mmkeys_handler.quit()
    remote.stop()
    fsiface.destroy()

    print_d("Shutting down player device %r." % player.version_info)
    player.destroy()
    tracker.destroy()
    quodlibet.library.save(force=True)

    config.save(const.CONFIG)

    print_d("Finished shutdown.")


if __name__ == "__main__":
    main()
