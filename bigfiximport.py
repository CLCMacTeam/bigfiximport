#!/usr/bin/env python
#
# Copyright 2015 The Pennsylvania State University.
#
"""
bigfiximport.py

Created by Matt Hansen (mah60@psu.edu) on 2015-02-28.

A utility for creating IBM Endpoint Manager (BigFix) tasks.
"""

import os
import sys
import argparse
import zipfile
import datetime
import mimetypes
import plistlib
import pkg_resources

# Needed to ignore some import errors
import __builtin__
from types import ModuleType

# -----------------------------------------------------------------------------
# Variables
# -----------------------------------------------------------------------------

__version__ = VERSION = '1.0'
MUNKI_ZIP = 'munki-master.zip'
MUNKILIB_PATH = 'munki-master/code/client/munkilib'

# -----------------------------------------------------------------------------
# Argument Parsing
# -----------------------------------------------------------------------------

parser = argparse.ArgumentParser(description='bigfiximport')
parser.add_argument('-v', '--verbose', action='count', dest='verbosity',
                    help='increase output verbosity', default=0)
parser.add_argument('--version', action='version', version='%(prog)s ' + __version__)

args = parser.parse_args()

# -----------------------------------------------------------------------------
# Platform Checks
# -----------------------------------------------------------------------------

if sys.platform.startswith('darwin'):
    PLATFORM = 'darwin'
    try:
        import Foundation
        DARWIN_FOUNDATION_AVAILABLE = True
    except ImportError:
        DARWIN_FOUNDATION_AVAILABLE = False
  
elif sys.platform.startswith('win'):
    PLATFORM = 'win'
elif sys.platform.startwith('linux'):
    PLATFORM = 'linux'
    
try:
    import besapi
    BESAPI_AVAILABLE = True
    besapi_version = pkg_resources.get_distribution("besapi").version
except ImportError:
    BESAPI_AVAILABLE = False

# Used to ignore some import errors
class DummyModule(ModuleType):
    def __getattr__(self, key):
        return None
    __all__ = []   # support wildcard imports

def tryimport(name, globals={}, locals={}, fromlist=[], level=-1):
    try:
        return realimport(name, globals, locals, fromlist, level)
    except ImportError:
        return DummyModule(name)

# Start ignoring import errors
if not DARWIN_FOUNDATION_AVAILABLE:
    realimport, __builtin__.__import__ = __builtin__.__import__, tryimport

if os.path.isfile(MUNKI_ZIP):
    with zipfile.ZipFile(MUNKI_ZIP, 'r') as munki_zip:
    
        version_plist = munki_zip.open("%s/%s" % (MUNKILIB_PATH, 'version.plist'), 'r')
        munkilib_version = plistlib.readPlist(version_plist).get('CFBundleShortVersionString')
        
        MUNKILIB_AVAILABLE = True
    
        for filename in munki_zip.namelist():
            if filename.startswith(MUNKILIB_PATH) and filename.endswith('.py'):
                 #munki_master_zip.open(filename, 'r').read()

# Verbose environment output
if args.verbosity > 1:
    for p in ['PLATFORM', 'BESAPI_AVAILABLE', 'MUNKILIB_AVAILABLE', 'DARWIN_FOUNDATION_AVAILABLE']:
        print "%s: %s" % (p, eval(p))
    
    if BESAPI_AVAILABLE:
        print "besapi version: %s" % besapi_version
    if MUNKILIB_AVAILABLE:
        print "munkilib version: %s" % munkilib_version

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def guess_file_type(url, use_strict=False):
     return mimetypes.guess_type(file_path, use_strict)

def print_zip_info(zf):
    for info in zf.infolist():
        print info.filename
        print '\tComment:\t', info.comment
        print '\tModified:\t', datetime.datetime(*info.date_time)
        print '\tSystem:\t\t', info.create_system, '(0 = Windows, 3 = Unix)'
        print '\tZIP version:\t', info.create_version
        print '\tCompressed:\t', info.compress_size, 'bytes'
        print '\tUncompressed:\t', info.file_size, 'bytes'
        print



print 'Number of arguments:', len(sys.argv), 'arguments.'
print 'Argument List:', str(sys.argv)

file_path = sys.argv[-1]
file_mime, file_encoding = guess_file_type(file_path)
file_is_local = True if os.path.isfile(file_path)  else False

# -----------------------------------------------------------------------------
# Core
# -----------------------------------------------------------------------------

if file_mime == 'application/x-apple-diskimage' and file_is_local and DARWIN_FOUNDATION_AVAILABLE:

    mounts = adobeutils.mountAdobeDmg(file_path)

    for mount in mounts:
        print adobeutils.findAdobePatchInstallerApp(mount)
        print adobeutils.getAdobeSetupInfo(mount)
        munkicommon.unmountdmg(mount)


if file_mime == 'application/zip' and file_is_local:
    zf = zipfile.ZipFile(file_path, 'r')
    print zf.namelist()
    print_zip_info(zf)
    
    


