# -*- coding: utf-8 -*-
# Copyright 2006 Markus Koller
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation
#
# $Id$

import locale
import os
import time
import gtk

import const
import qltk
import stock
import util

from devices._base import Device
from formats._audio import AudioFile

# Wraps an itdb_track from libgpod in an AudioFile instance
class IPodSong(AudioFile):
    is_file = False

    def __init__(self, track):
        super(IPodSong, self).__init__()
        self.sanitize(gpod.itdb_filename_on_ipod(track))
        self.itdb_id = track.id

        for key in ['artist', 'album', 'title', 'genre', 'grouping']:
            value = getattr(track, key)
            if value:
                try: self[key] = unicode(value)
                except UnicodeDecodeError:
                    self[key] = unicode(value, errors='replace')
        for key in ['bitrate', 'playcount']:
            value = getattr(track, key)
            if value:
                self['~#'+key] = value

        try: self["date"] = unicode(track.year)
        except AttributeError: pass

        if track.cds:
            self["discnumber"] = u"%d/%d" % (track.cd_nr, track.cds)
        elif track.cd_nr:
            self["discnumber"] = u"%d" % track.cd_nr

        if track.tracks:
            self['tracknumber'] = u"%d/%d" % (track.track_nr, track.tracks)
        elif track.track_nr:
            self['tracknumber'] = u"%d" % track.track_nr

        for key, value in {
            '~#rating': track.rating / 100.0,
            '~#length': track.tracklen / 1000.0,
        }.items():
            if value != 0:
                self[key] = value
        self['~format'] = u"iPod: %s" % track.filetype

    # Disable all tag editing
    def can_change(self, k=None):
        return []

class IPodDevice(Device):
    icon = stock.IPOD
    type = "ipod"

    ordered = True

    defaults = {
        'gain': 0.0,
        'covers': True,
        'all_tags': False,
        'title_version': False,
        'album_part': False,
    }

    __itdb = None
    __covers = []

    def Properties(self):
        props = []

        gain = gtk.SpinButton()
        gain.set_range(-20, 20)
        gain.set_digits(1)
        gain.set_increments(0.1, 1)
        gain.set_value(float(self['gain']))
        props.append((_("_Volume Gain (dB):"), gain, 'gain'))

        for key, label in [
            ['covers', _("_Copy album covers")],
            ['all_tags', _("Combine tags with _multiple values")],
            ['title_version', _("Title includes _version")],
            ['album_part', _("Album includes _part")],
        ]:
            check = gtk.CheckButton()
            check.set_active(self[key])
            props.append((label, check, key))

        if self.is_connected():
            details = self.__get_details()
            if len(details) > 0:
                props.append((None, None, None))
            if 'model' in details:
                props.append((_("Model:"), details['model'], None))
            if 'space' in details:
                props.append((_("Capacity:"), details['space'], None))
            if 'firmware' in details:
                props.append((_("Firmware:"), details['firmware'], None))

        return props

    def __get_details(self):
        d = {}
        sysinfo = os.path.join(self.mountpoint,
            'iPod_Control', 'Device', 'SysInfo')

        if os.path.isfile(sysinfo):
            file = open(sysinfo)
            while True:
                line = file.readline()
                if not line: break
                parts = line.split()
                if len(parts) < 2: continue

                parts[0] = parts[0].rstrip(":")
                if parts[0] == "ModelNumStr" and parts[1] in self.__models:
                    d['model'], d['space'] = self.__models[parts[1]]
                elif parts[0] == "visibleBuildID":
                    d['firmware'] = parts[2].strip("()")
            file.close()
        else:
            # Assume an iPod shuffle
            info = os.statvfs(self.mountpoint)
            space = info.f_bsize * info.f_blocks
            if space > 512 * 1024 * 1024:
                model = 'M9725'
            else:
                model = 'M9724'
            if model in self.__models:
                d['model'], d['space'] = self.__models[model]

        return d

    def list(self, wlb):
        self.__load_db()
        songs = []
        orphaned = False
        for track in gpod.sw_get_tracks(self.__itdb):
            filename = gpod.itdb_filename_on_ipod(track)
            if filename:
                songs.append(IPodSong(track))
            else: # Remove orphaned iTunesDB track
                orphaned = True
                print _("W: removing orphaned iPod track")
                self.__remove_track(track)
        if orphaned:
            self.__save_db()
        self.__close_db()
        return songs

    def copy(self, songlist, song):
        self.__load_db()
        track = gpod.itdb_track_new()

        # Either combine tags with comma, or only take the first value
        if self['all_tags']: tag = song.comma
        else: tag = lambda key: song.list(key)[0]

        title = tag('title')
        if self['title_version']:
            title = " - ".join([title, song('version')])
        track.title = str(title)

        album = tag('album')
        if self['album_part']:
            album = " - ".join([album, song('part')])
        track.album = str(album)

        # String keys
        for key in ['artist', 'genre', 'grouping']:
            try: setattr(track, key, str(tag(key)))
            except IndexError: continue
        # Numeric keys
        for key in ['bitrate', 'playcount', 'year']:
            try: setattr(track, key, int(song('~#'+key)))
            except ValueError: continue
        # Numeric keys where the names differ
        for key, value in {
            'cd_nr':         song('~#disc'),
            'cds':           song('~#discs'),
            'rating':        song('~#rating') * 100,
            'time_added':    self.__mactime(time.time()),
            'time_modified': self.__mactime(util.mtime(song('~filename'))),
            'track_nr':      song('~#track'),
            'tracklen':      song('~#length') * 1000,
            'tracks':        song('~#tracks'),
            'size':          util.size(song('~filename')),
            'soundcheck':    self.__soundcheck(song),
        }.items():
            try: setattr(track, key, int(value))
            except ValueError: continue

        track.filetype = song('~format')
        track.comment = song('~filename')

        # Associate a cover with the track
        if self['covers']:
            cover = song.find_cover()
            if cover:
                # libgpod will copy the file later when the iTunesDB
                # is saved, so we have to keep a reference around in
                # case the cover is a temporary file.
                self.__covers.append(cover)
                gpod.itdb_track_set_thumbnails(track, cover.name)

        # Add the track to the master playlist
        gpod.itdb_track_add(self.__itdb, track, -1)
        master = gpod.itdb_playlist_mpl(self.__itdb)
        gpod.itdb_playlist_add_track(master, track, -1)

        # Copy the actual file
        if gpod.itdb_cp_track_to_ipod(track, song['~filename'], None) == 1:
            return IPodSong(track)
        else:
            return False

    def delete(self, songlist, song):
        self.__load_db()
        try:
            track = gpod.itdb_track_by_id(self.__itdb, song.itdb_id)
            if track and gpod.itdb_filename_on_ipod(track) == song['~filename']:
                os.remove(song['~filename'])
                self.__remove_track(track)
        except IOError, exc:
            return str(exc).decode(locale.getpreferredencoding(), 'replace')
        else: return True

    def cleanup(self, wlb, action):
        try:
            wlb.set_text("<b>Saving iPod database...</b>")
            if not self.__save_db():
                wlb.set_text(_("Unable to save iPod database"))
                return False
            return True
        finally:
            self.__close_db()
            self.__covers = []

    def __load_db(self):
        if self.__itdb: return self.__itdb

        self.__itdb = gpod.itdb_parse(self.mountpoint, None)
        if not self.__itdb and self.is_connected() and qltk.ConfirmAction(
            qltk.get_top_parent(self), _("Uninitialized iPod"),
            _("Do you want to create an empty database on this iPod?")
            ).run():
            self.__itdb = self.create_db()

        return self.__itdb

    def __save_db(self):
        if gpod.itdb_write(self.__itdb, None) == 1 and \
           gpod.itdb_shuffle_write(self.__itdb, None) == 1:
            return True
        else:
            return False

    def __create_db(self):
        db = gpod.itdb_new();
        gpod.itdb_set_mountpoint(self.mountpoint)

        master = gpod.itdb_playlist_new('iPod', False)
        gpod.itdb_playlist_set_mpl(master)
        gpod.itdb_playlist_add(db, master, 0)

        return db

    def __close_db(self):
        if self.__itdb: gpod.itdb_free(self.__itdb)
        self.__itdb = None

    def __remove_track(self, track):
        master = gpod.itdb_playlist_mpl(self.__itdb)
        gpod.itdb_playlist_remove_track(master, track)
        gpod.itdb_track_remove(track)

    def __mactime(self, time):
        time = int(time)
        if time == 0: return time
        else: return time + 2082844800

    # Convert ReplayGain values to Apple Soundcheck values
    def __soundcheck(self, song):
        if 'replaygain_album_gain' in song:
            db = float(song['replaygain_album_gain'].split()[0])
        elif 'replaygain_track_gain' in song:
            db = float(song['replaygain_track_gain'].split()[0])
        else: db = 0.0

        soundcheck = int(round(1000 * 10.**(
            -0.1 * (db + float(self['gain'])))))
        return soundcheck

    # This list is taken from
    # http://en.wikipedia.org/wiki/List_of_iPod_model_numbers
    __models = {
        # First Generation
        'M8513': ('iPod', '5GB'),
        'M8541': ('iPod', '5GB'),
        'M8697': ('iPod', '5GB'),
        'M8709': ('iPod', '10GB'),
        # Second Generation
        'M8737': ('iPod', '10GB'),
        'M8740': ('iPod', '10GB'),
        'M8738': ('iPod', '20GB'),
        'M8741': ('iPod', '20GB'),
        # Third Generation
        'M8976': ('iPod', '10GB'),
        'M8946': ('iPod', '15GB'),
        'M9460': ('iPod', '15GB'),
        'M9244': ('iPod', '20GB'),
        'M8948': ('iPod', '30GB'),
        'M9245': ('iPod', '40GB'),
        # Fourth Generation
        'M9282': ('iPod', '20GB'),
        'M9787': ('iPod (U2 edition)', '20GB'),
        'M9268': ('iPod', '40GB'),
        # Photo / Fourth Generation
        'MA079': ('iPod photo', '20GB'),
        'MA127': ('iPod photo (U2 edition)', '20GB'),
        'M9829': ('iPod photo', '30GB'),
        'M9585': ('iPod photo', '40GB'),
        'M9586': ('iPod photo', '60GB'),
        'M9830': ('iPod photo', '60GB'),
        # Shuffle / Fourth Generation
        'M9724': ('iPod shuffle', '512MB'),
        'M9725': ('iPod shuffle', '1GB'),
        'MA133': ('iPod shuffle', '512MB'),
        # Video / Fifth Generation
        'MA002': ('iPod video white', '30GB'),
        'MA146': ('iPod video black', '30GB'),
        'MA003': ('iPod video white', '60GB'),
        'MA147': ('iPod video black', '60GB'),
        # Nano / Fifth Generation
        'MA350': ('iPod nano white', '1GB'),
        'MA352': ('iPod nano black', '1GB'),
        'MA004': ('iPod nano white', '2GB'),
        'MA099': ('iPod nano black', '2GB'),
        'MA005': ('iPod nano white', '4GB'),
        'MA107': ('iPod nano black', '4GB'),
        # First Generation Mini
        'M9160': ('iPod mini silver', '4GB'),
        'M9436': ('iPod mini blue', '4GB'),
        'M9435': ('iPod mini pink', '4GB'),
        'M9434': ('iPod mini green', '4GB'),
        'M9437': ('iPod mini gold', '4GB'),
        # Second Generation Mini
        'M9800': ('iPod mini silver', '4GB'),
        'M9802': ('iPod mini blue', '4GB'),
        'M9804': ('iPod mini pink', '4GB'),
        'M9806': ('iPod mini green', '4GB'),
        'M9801': ('iPod mini silver', '6GB'),
        'M9803': ('iPod mini blue', '6GB'),
        'M9805': ('iPod mini pink', '6GB'),
        'M9807': ('iPod mini green', '6GB'),
    }

try: import gpod
except ImportError:
    devices = []
else:
    devices = [IPodDevice]
