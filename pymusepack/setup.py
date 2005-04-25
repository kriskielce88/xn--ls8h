#!/usr/bin/env python

from distutils.core import setup, Extension
setup(name = "pymusepack", version = "0.2",
      url = "http://sacredchao.net/~piman/software/python.shtml",
      description = "Musepack decoder and tagger",
      author = "Joe Wreschnig",
      author_email = "piman@sacredchao.net",
      license = "GNU GPL v2",
      long_description = """
This Python module lets you load and decode Musepack (MPC/MP+)
files using libmusepack. It resembles the Python MAD, Vorbis,
and ModPlug interfaces.""",
      packages = ["musepack"],
      ext_modules=[Extension('musepack.mpc', ['musepack/mpc.c'],
                             libraries = ['musepack'])]
    )
