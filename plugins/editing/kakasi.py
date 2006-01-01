import os, gtk, gobject
import util
from plugins.editing import RenameFilesPlugin

class Kakasi(RenameFilesPlugin, gtk.CheckButton):
    __gsignals__ = {
        "preview": (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ())
        }
    def __init__(self):
        super(Kakasi, self).__init__("Romanize _Japanese text")
        self.connect_object('toggled', self.emit, 'preview')

    active = property(lambda s: s.get_active())

    def filter(self, value):
        try: data = value.encode('shift-jis', 'replace')
        except None: return value
        line = ("kakasi -isjis -osjis -Ha -Ka -Ja -Ea -ka -s")
        w, r = os.popen2(line.split())
        w.write(data)
        w.close()
        try: return r.read().decode('shift-jis').strip()
        except: return value

if not util.iscommand("kakasi"): del(Kakasi)
else: gobject.type_register(Kakasi)