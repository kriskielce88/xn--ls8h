# -*- coding: utf-8 -*-
# Copyright 2013 Simonas Kazlauskas
#           2014 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation

from itertools import chain

from quodlibet import config
from quodlibet.plugins import PluginManager, PluginHandler
from quodlibet.util.cover import built_in
from quodlibet.plugins.cover import CoverSourcePlugin


class CoverPluginHandler(PluginHandler):
    def __init__(self, use_built_in=True):
        self.providers = set()
        if use_built_in:
            self.built_in = set([built_in.EmbedCover,
                                 built_in.FilesystemCover])
        else:
            self.built_in = set()

    def init_plugins(self):
        PluginManager.instance.register_handler(self)

    def plugin_handle(self, plugin):
        return issubclass(plugin.cls, CoverSourcePlugin)

    def plugin_enable(self, plugin):
        self.providers.add(plugin)
        print_d("Registered {0} cover source".format(plugin.cls.__name__))

    def plugin_disable(self, plugin):
        self.providers.remove(plugin)
        print_d("Unregistered {0} cover source".format(plugin.cls.__name__))

    @property
    def sources(self):
        sources = chain((p.cls for p in self.providers), self.built_in)
        for p in sorted(sources, reverse=True, key=lambda x: x.priority()):
            yield p

    def acquire_cover(self, callback, cancellable, song):
        """
        Try to get covers from all cover sources until a cover is found.

        * callback(found, result) is the function which will be called when
        this method completes its job.
        * cancellable – Gio.Cancellable which will interrupt the search.
        The callback won't be called when the operation is cancelled.
        """
        sources = self.sources

        def success(source, result):
            name = source.__class__.__name__
            print_d('Successfully got cover from {0}'.format(name))
            source.disconnect_by_func(success)
            source.disconnect_by_func(failure)
            if not cancellable or not cancellable.is_cancelled():
                callback(True, result)

        def failure(source, msg):
            name = source.__class__.__name__
            print_d("Didn't get cover from {0}: {1}".format(name, msg))
            source.disconnect_by_func(success)
            source.disconnect_by_func(failure)
            if not cancellable or not cancellable.is_cancelled():
                run()

        def run():
            try:
                provider = next(sources)(song, cancellable)
            except StopIteration:
                return callback(False, None)  # No cover found

            cover = provider.cover
            if cover:
                name = provider.__class__.__name__
                print_d('Found local cover from {0}'.format(name))
                callback(True, cover)
            else:
                provider.connect('fetch-success', success)
                provider.connect('fetch-failure', failure)
                provider.fetch_cover()
        if not cancellable or not cancellable.is_cancelled():
            run()

    def acquire_cover_sync(self, song, embedded=True, external=True):
        """Gets *cached* cover synchronously.

        As CoverSource fetching functionality is asynchronous it is only
        possible to check for already fetched cover.
        """

        return self.acquire_cover_sync_many([song], embedded, external)

    def acquire_cover_sync_many(self, songs, embedded=True, external=True):
        """Same as acquire_cover_sync but returns a cover for multiple
        images"""

        for plugin in self.sources:
            if not embedded and plugin.embedded:
                continue
            if not external and not plugin.embedded:
                continue

            groups = {}
            for song in songs:
                groups.setdefault(plugin.group_by(song), []).append(song)

            # sort both groups and songs by key, so we always get
            # the same result for the same set of songs
            for key, group in sorted(groups.items()):
                song = sorted(group, key=lambda s: s.key)[0]
                cover = plugin(song).cover
                if cover:
                    return cover

    def get_cover(self, song):
        """Returns a cover file object for one song or None.

        Compared to acquire_cover_sync() this respects the prefer_embedded
        setting.
        """

        return self.get_cover_many([song])

    def get_cover_many(self, songs):
        """Returns a cover file object for many songs or None.

        Returns the first found image for a group of songs
        and respects the prefer_embedded setting. It tries to return the
        same cover for the same set of songs.
        """

        prefer_embedded = config.getboolean(
            "albumart", "prefer_embedded", False)

        get = self.acquire_cover_sync_many
        if prefer_embedded:
            return get(songs, True, False) or get(songs, False, True)
        else:
            return get(songs, False, True) or get(songs, True, False)
