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
import re
import sys
import shutil
import argparse
import getpass
import zipfile
import tempfile
import datetime
import mimetypes
import plistlib
import hashlib
import pkg_resources

from time import gmtime, strftime
from xml.etree import ElementTree as ET
from ConfigParser import SafeConfigParser

import requests
try:
    requests.packages.urllib3.disable_warnings()
except:
    pass

# Needed to ignore some import errors
import __builtin__
from types import ModuleType

# -----------------------------------------------------------------------------
# Templates
# -----------------------------------------------------------------------------

from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('templates'))


# -----------------------------------------------------------------------------
# Variables
# -----------------------------------------------------------------------------

__version__ = VERSION = '1.0'
MUNKI_ZIP = 'munki-master.zip'
MUNKILIB_PATH = os.path.join('munki-master', 'code', 'client', 'munkilib')

# -----------------------------------------------------------------------------
# Argument Parsing
# -----------------------------------------------------------------------------

parser = argparse.ArgumentParser(description='bigfiximport')
parser.add_argument('-v', '--verbose', action='count', dest='verbosity',
                    help='increase output verbosity', default=0)
parser.add_argument('--version', action='version', version='%(prog)s ' + __version__)

args, extra_args = parser.parse_known_args()

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

if os.path.isdir('munkilib'):
    MUNKILIB_AVAILABLE = True
    munkilib_version = plistlib.readPlist(os.path.join('munkilib', 'version.plist')).get('CFBundleShortVersionString')

    from munkilib import utils
    from munkilib import munkicommon
    from munkilib import adobeutils

    if DARWIN_FOUNDATION_AVAILABLE:
        from munkilib import FoundationPlist
        from munkilib import appleupdates
        from munkilib import profiles
        from munkilib import fetch

# Verbose environment output
if args.verbosity > 1:
    for p in ['PLATFORM', 'BESAPI_AVAILABLE', 'MUNKILIB_AVAILABLE', 'DARWIN_FOUNDATION_AVAILABLE']:
        print "%s: %s" % (p, eval(p))

    if BESAPI_AVAILABLE:
        print "besapi version: %s" % besapi_version
    if MUNKILIB_AVAILABLE:
        print "munkilib version: %s" % munkilib_version

# -----------------------------------------------------------------------------
# besapi Config
# TODO: Make config paths work cross platform
# -----------------------------------------------------------------------------

# Read Config File
CONFPARSER = SafeConfigParser({'VERBOSE': 'True'})
if PLATFORM is 'win':
    system_wide_conf_path = os.path.join(os.environ['ALLUSERSPROFILE'], 'besapi.conf')
    CONFPARSER.read([system_wide_conf_path,
                     os.path.expanduser('~/besapi.conf'),
                     'besapi.conf'])
else:
    CONFPARSER.read(['/etc/besapi.conf',
                     os.path.expanduser('~/besapi.conf'),
                     'besapi.conf'])

BES_ROOT_SERVER = CONFPARSER.get('besapi', 'BES_ROOT_SERVER')
BES_USER_NAME = CONFPARSER.get('besapi', 'BES_USER_NAME')
BES_PASSWORD = CONFPARSER.get('besapi', 'BES_PASSWORD')

if 'besarchiver' in CONFPARSER.sections():
    VERBOSE = CONFPARSER.getboolean('besarchiver', 'VERBOSE')
else:
    VERBOSE = True
    
B = besapi.BESConnection(BES_USER_NAME, BES_PASSWORD, BES_ROOT_SERVER)

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
        
def get_env_source_mime_data():
    return {
                'today'     : str(datetime.datetime.now())[:10],
                'strftime'  : strftime("%a, %d %b %Y %X +0000", gmtime()),
                'user'      : getpass.getuser()
            }

def get_sha1_size(file_path):
    return {
                'sha1'  : hashlib.sha1(file(file_path).read()).hexdigest(),
                'size'  : os.path.getsize(file_path)
            }

def getiteminfo(itempath):
    """
    Gets info for filesystem items passed to makecatalog item, to be used for
    the "installs" key.
    Determines if the item is an application, bundle, Info.plist, or a file or
    directory and gets additional metadata for later comparison.
    """
    infodict = {}
    if munkicommon.isApplication(itempath):
        infodict['type'] = 'application'
        infodict['path'] = itempath
        plist = getBundleInfo(itempath)
        for key in ['CFBundleName', 'CFBundleIdentifier',
                    'CFBundleShortVersionString', 'CFBundleVersion']:
            if key in plist:
                infodict[key] = plist[key]
        if 'LSMinimumSystemVersion' in plist:
            infodict['minosversion'] = plist['LSMinimumSystemVersion']
        elif 'SystemVersionCheck:MinimumSystemVersion' in plist:
            infodict['minosversion'] = \
                plist['SystemVersionCheck:MinimumSystemVersion']
        else:
            infodict['minosversion'] = '10.6'

    elif os.path.exists(os.path.join(itempath, 'Contents', 'Info.plist')) or \
         os.path.exists(os.path.join(itempath, 'Resources', 'Info.plist')):
        infodict['type'] = 'bundle'
        infodict['path'] = itempath
        plist = getBundleInfo(itempath)
        for key in ['CFBundleShortVersionString', 'CFBundleVersion']:
            if key in plist:
                infodict[key] = plist[key]

    elif itempath.endswith("Info.plist") or \
         itempath.endswith("version.plist"):
        infodict['type'] = 'plist'
        infodict['path'] = itempath
        try:
            plist = FoundationPlist.readPlist(itempath)
            for key in ['CFBundleShortVersionString', 'CFBundleVersion']:
                if key in plist:
                    infodict[key] = plist[key]
        except FoundationPlist.NSPropertyListSerializationException:
            pass

    # let's help the admin -- if CFBundleShortVersionString is empty
    # or doesn't start with a digit, and CFBundleVersion is there
    # use CFBundleVersion as the version_comparison_key
    if (not infodict.get('CFBundleShortVersionString') or
        infodict['CFBundleShortVersionString'][0]
        not in '0123456789'):
        if infodict.get('CFBundleVersion'):
            infodict['version_comparison_key'] = 'CFBundleVersion'
    elif 'CFBundleShortVersionString' in infodict:
        infodict['version_comparison_key'] = 'CFBundleShortVersionString'

    if not 'CFBundleShortVersionString' in infodict and \
       not 'CFBundleVersion' in infodict:
        infodict['type'] = 'file'
        infodict['path'] = itempath
        if os.path.isfile(itempath):
            infodict['md5checksum'] = munkicommon.getmd5hash(itempath)
    return infodict
    
def getBundleInfo(path):
    """
    Returns Info.plist data if available
    for bundle at path
    """
    infopath = os.path.join(path, "Contents", "Info.plist")
    if not os.path.exists(infopath):
        infopath = os.path.join(path, "Resources", "Info.plist")

    if os.path.exists(infopath):
        try:
            plist = FoundationPlist.readPlist(infopath)
            return plist
        except FoundationPlist.NSPropertyListSerializationException:
            pass

    return None

# -----------------------------------------------------------------------------
# Core
# -----------------------------------------------------------------------------
if args.verbosity > 1:
    print 'Number of arguments:', len(sys.argv), 'arguments.'
    print 'Argument List:', str(sys.argv)

file_path = sys.argv[-1]
file_path_noextension = os.path.splitext(file_path)[0]
file_mime, file_encoding = guess_file_type(file_path)
file_is_local = True if os.path.isfile(file_path) else False

file_name = os.path.basename(file_path)
file_name_noextension, file_extension = os.path.splitext(file_name)
base_file_name = file_name.split('-')[0].split('.')[0]

if 'adobe' in file_path.lower():
    IS_ADOBE_UPDATE = True
else:
    IS_ADOBE_UPDATE = False

# -----------------------------------------------------------------------------
# OS X Drag & Drop App
# -----------------------------------------------------------------------------
if file_mime == 'application/x-apple-diskimage' and file_is_local and DARWIN_FOUNDATION_AVAILABLE and not IS_ADOBE_UPDATE:
    template = env.get_template('copyfromdmg.bes')
    mountpoints = munkicommon.mountdmg(file_path, use_existing_mounts=True)

    for (itemname, dummy_dirs, dummy_files) in os.walk(mountpoints[0]):
        itempath = os.path.join(mountpoints[0], itemname)
        if munkicommon.isApplication(itempath):
            item = itemname
            iteminfo = getiteminfo(itempath)
            if iteminfo:
                break

    if iteminfo:
        if os.path.isabs(item):
            mountpointPattern = "^%s/" % mountpoints[0]
            item = re.sub(mountpointPattern, '', item)

        cataloginfo = {}
        cataloginfo['display_name'] = iteminfo.get('CFBundleName',
                                        os.path.splitext(item)[0])
        version_comparison_key = iteminfo.get(
            'version_comparison_key', "CFBundleShortVersionString")
        cataloginfo['version'] = \
            iteminfo.get(version_comparison_key, "0")
        cataloginfo.update(iteminfo)
        cataloginfo['item_to_copy'] = item
        cataloginfo['base_file_name'] = base_file_name
        cataloginfo.update(get_sha1_size(file_path))
        cataloginfo.update(get_env_source_mime_data())

    #eject the dmg
    munkicommon.unmountdmg(mountpoints[0])
    new_task = B.post('tasks/custom/SysManDev', template.render(**cataloginfo))

# -----------------------------------------------------------------------------
# Adobe Updates
# -----------------------------------------------------------------------------

# Mac Adobe Update (.dmg)
if file_mime == 'application/x-apple-diskimage' and file_is_local and DARWIN_FOUNDATION_AVAILABLE and IS_ADOBE_UPDATE:
    template = env.get_template('ccupdatemacosx.bes')
    mounts = adobeutils.mountAdobeDmg(file_path)

    for mount in mounts:
        adobe_info = adobeutils.getAdobeSetupInfo(mount)
        adobe_info['adobepatchinstaller'] = adobeutils.findAdobePatchInstallerApp(mount)

        mountpointPattern = "^%s/" % mount
        adobepatchinstaller = re.sub(mountpointPattern, '', adobe_info['adobepatchinstaller'])

        for (path, dummy_dirs, dummy_files) in os.walk(mount):
            if path.endswith('/payloads'):
                payloads_dir = path

        with open(os.path.join(payloads_dir, 'UpdateManifest.xml'), 'r') as setupfile:
            root = ET.parse(setupfile).getroot()
            adobe_info['description'] = root.find('''.//Description/en_US''').text.replace(u'\xa0', u' ')
        
        munkicommon.unmountdmg(mount)

# Windows Adobe Update (.zip)
elif file_mime == 'application/zip' and file_is_local and IS_ADOBE_UPDATE:
    
    # Pick template based on '64bit' or '32bit' in file_path
    if any(x in file_path for x in ['64Bit', '64bit', 'X64', 'x64']):
        template = env.get_template('ccupdatewindows64.bes')
    elif any(x in file_path for x in ['32Bit', '32bit']):
        template = env.get_template('ccupdatewindows32.bes')
    else:
        template = env.get_template('ccupdatewindows.bes')

    zf = zipfile.ZipFile(file_path, 'r')
    extractdir = os.path.join(tempfile.gettempdir(), file_name_noextension)

    for name in zf.namelist():
        if not name.endswith('.zip') and not name.endswith('.exe'):
            if name.endswith('Setup.xml') or name.endswith('setup.xml'):
                setup_xml = name
            elif name.endswith('UpdateManifest.xml'):
                update_manifest = name
            
            (dirname, filename) = os.path.split(name)
            zf.extract(name, extractdir)

    adobe_info = adobeutils.getAdobeSetupInfo(extractdir)
    adobe_info['adobepatchinstaller'] = 'AdobePatchInstaller.exe'

    try:
        with open(os.path.join(extractdir, setup_xml), 'r') as setupfile:
            root = ET.parse(setupfile).getroot()
            adobe_info['display_name'] = root.find('''.//Media/Volume/Name''').text
    except AttributeError:
        pass # Can't find display name, so we'll get it from UpdateManifest next
    
    with open(os.path.join(extractdir, update_manifest), 'r') as manifestfile:
        root = ET.parse(manifestfile).getroot()
        adobe_info['version'] = root.find('''.//UpdateID''').text
        adobe_info['description'] = root.find('''.//Description/en_US''').text.replace(u'\xa0', u' ')
        
        # Failed to get display_name from Setup.xml, so look in UpdateManifest
        if not adobe_info.get('display_name'):
            adobe_info['display_name'] = root.find('''.//DisplayName/en_US''').text

    shutil.rmtree(extractdir)

# Process Adobe Update
if 'adobe_info' in locals():
    
    # Get direct download link from url file
    with open('.'.join([file_path, 'url']), 'r') as url_file:
        adobe_info['url'] = url_file.readline()
    
    # Trim description
    if ':' in adobe_info['description']:
        adobe_info['description'] = adobe_info['description'].split(' : ', 1)[-1]
    
    # Sanitize and workaround Adobe naming inconsistency
    adobe_info['name'] = ''.join(adobe_info['display_name'].split('.')[0])
    if 'Flash' in adobe_info['name'] and 'Professional ' in adobe_info['name']:
        adobe_info['name'] = adobe_info['name'].replace('Professional ', '')

    # Determine base version
    adobe_info['base_version'] = "%s.0.0" % adobe_info['version'].split('.')[0]

    adobe_info['base_file_name'] = base_file_name
    adobe_info.update(get_env_source_mime_data())
    adobe_info.update(get_sha1_size(file_path))

    # Render and POST new task to console site
    new_task = B.post('tasks/custom/SysManDev', template.render(**adobe_info))

# Reporting Output
if 'new_task' in locals(): 
    if len(new_task()):
        print "\nNew Task: %s - %s" % (str(new_task().Task.Name), str(new_task().Task.ID))
    else:
        print new_task
