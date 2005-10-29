#!/usr/bin/env python
# Copyright 2004-2005 Joe Wreschnig, Niklas Janlert
# <quodlibet@lists.sacredchao.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# $Id$

import os, sys

if __name__ == "__main__":
    import locale, gettext
    gettext.bindtextdomain("quodlibet")
    gettext.textdomain("quodlibet")
    gettext.install("quodlibet", unicode=True)
    try: locale.setlocale(locale.LC_ALL, '')
    except: pass

    basedir = os.path.dirname(os.path.realpath(__file__))
    if not os.path.exists(os.path.join(basedir, "exfalso.py")):
        if os.path.exists(os.path.join(os.getcwd(), "exfalso.py")):
            basedir = os.getcwd()
    sys.path.insert(1, os.path.join(basedir, "quodlibet.zip"))

    import const
    import util
    opts = util.OptionParser(
        "Ex Falso", const.VERSION,
        _("an audio tag editor"), "[%s]" % _("directory"))

    import config
    config.init(const.CONFIG)

    sys.argv.append(os.path.abspath("."))
    opts, args = opts.parse()
    args[0] = os.path.realpath(args[0])
    os.chdir(basedir)

    import pygtk
    pygtk.require('2.0')
    import gtk, qltk, efwidgets
    w = efwidgets.ExFalsoWindow(qltk.SongWatcher(), args[0])
    w.connect('destroy', gtk.main_quit)
    w.show_all()

    if (os.path.exists(const.CONTROL) and
        not config.getboolean('exfalso', 'shutup')):
        qltk.WarningMessage(
            w, _("Quod Libet is running"),
            _("It looks like you are running Quod Libet right now. "
              "If you edit songs also in Quod Libet's library while it is "
              "running, you may need to refresh or re-add them.\n\n"
              "If you are not running Quod Libet, or are editing songs "
              "outside of its library, you may ignore this warning.")).run()

    gtk.main()
