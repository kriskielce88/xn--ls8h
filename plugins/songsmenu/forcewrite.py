# Copyright 2005 Joe Wreschnig
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation
#
# $Id$

from plugins.songsmenu import SongsMenuPlugin

class ForceWrite(SongsMenuPlugin):
    PLUGIN_NAME = "Force Write"
    PLUGIN_DESC = ("Save the files again. This will make sure play counts "
                   "and ratings are up-to-date.")
    PLUGIN_ICON = 'gtk-save'
    PLUGIN_VERSION = "0.14"

    def plugin_song(self, song):
        song._needs_write = True