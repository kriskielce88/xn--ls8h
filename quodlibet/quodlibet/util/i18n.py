# -*- coding: utf-8 -*-
# Copyright 2006 Joe Wreschnig
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation

import __builtin__
import gettext
import os


def gettext_install_dummy(unicode=False):
    """Installs dummy gettext functions into the builtins namespace"""

    if unicode:
        uni = type(u"")
    else:
        uni = lambda v: v

    _dummy_gettext = lambda value: uni(value)
    _dummy_pgettext = lambda context, value: uni(value)
    _dummy_ngettext = lambda v1, v2, count: (count == 1) and uni(v1) or uni(v2)
    _dummy_npgettext = lambda context, v1, v2, count: \
        (count == 1) and uni(v1) or uni(v2)
    __builtin__.__dict__["_"] = _dummy_gettext
    __builtin__.__dict__["C_"] = _dummy_pgettext
    __builtin__.__dict__["N_"] = _dummy_gettext
    __builtin__.__dict__["ngettext"] = _dummy_ngettext
    __builtin__.__dict__["npgettext"] = _dummy_npgettext


class GlibTranslations(gettext.GNUTranslations):
    """Provide a glib-like translation API for Python.

    This class adds support for pgettext (and upgettext) mirroring
    glib's C_ macro, which allows for disambiguation of identical
    source strings. It also installs N_, C_, and ngettext into the
    __builtin__ namespace.

    It can also be instantiated and used with any valid MO files
    (though it won't be able to translate anything, of course).
    """

    def __init__(self, fp=None):
        self.path = (fp and fp.name) or ""
        self._catalog = {}
        self.plural = lambda n: n != 1
        gettext.GNUTranslations.__init__(self, fp)

    def ugettext(self, message):
        # force unicode here since __contains__ (used in gettext) ignores
        # our changed defaultencoding for coercion, so utf-8 encoded strings
        # fail at lookup.
        message = unicode(message)
        return gettext.GNUTranslations.ugettext(self, message)

    def ungettext(self, msgid1, msgid2, n):
        # see ugettext
        msgid1 = unicode(msgid1)
        msgid2 = unicode(msgid2)
        return gettext.GNUTranslations.ungettext(self, msgid1, msgid2, n)

    def pgettext(self, context, msgid):
        real_msgid = "%s\x04%s" % (context, msgid)
        result = self.gettext(real_msgid)
        if result == real_msgid:
            return msgid
        return result

    def npgettext(self, context, msgid, msgidplural, n):
        real_msgid = "%s\x04%s" % (context, msgid)
        real_msgidplural = "%s\x04%s" % (context, msgidplural)
        result = self.ngettext(real_msgid, real_msgidplural, n)
        if result == real_msgid:
            return msgid
        elif result == real_msgidplural:
            return msgidplural
        return result

    def unpgettext(self, context, msgid, msgidplural, n):
        context = unicode(context)
        msgid = unicode(msgid)
        msgidplural = unicode(msgidplural)
        real_msgid = u"%s\x04%s" % (context, msgid)
        real_msgidplural = u"%s\x04%s" % (context, msgidplural)
        result = self.ngettext(real_msgid, real_msgidplural, n)
        if result == real_msgid:
            return msgid
        elif result == real_msgidplural:
            return msgidplural
        return result

    def upgettext(self, context, msgid):
        context = unicode(context)
        msgid = unicode(msgid)
        real_msgid = u"%s\x04%s" % (context, msgid)
        result = self.ugettext(real_msgid)
        if result == real_msgid:
            return msgid
        return result

    def install(self, unicode=False):
        # set by tests
        if "QUODLIBET_NO_TRANS" in os.environ:
            return

        if unicode:
            _ = self.ugettext
            ngettext = self.ungettext
            npgettext = self.unpgettext
            _C = self.upgettext
            _N = type(u"")
        else:
            _ = self.gettext
            ngettext = self.ngettext
            npgettext = self.npgettext
            _C = self.pgettext
            _N = lambda s: s

        test_key = "QUODLIBET_TEST_TRANS"
        if test_key in os.environ:
            text = os.environ[test_key]

            def wrap(f):
                def g(*args):
                    return text + f(*args) + text
                return g

            _ = wrap(_)
            _N = wrap(_N)
            _C = wrap(_C)
            ngettext = wrap(ngettext)
            npgettext = wrap(npgettext)

        __builtin__.__dict__["_"] = _
        __builtin__.__dict__["N_"] = _N
        __builtin__.__dict__["C_"] = _C
        __builtin__.__dict__["ngettext"] = ngettext
        __builtin__.__dict__["npgettext"] = npgettext
