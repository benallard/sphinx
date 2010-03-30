# -*- coding: utf-8 -*-
"""
    sphinx.builders.epub
    ~~~~~~~~~~~~~~~~~~~~

    Build epub files.
    Originally derived from qthelp.py.

    :copyright: Copyright 2007-2010 by the Sphinx team, see AUTHORS.
    :license: BSD, see LICENSE for details.
"""

import os
import codecs
from os import path
import zipfile

from docutils import nodes
from docutils.transforms import Transform

from sphinx.builders.html import StandaloneHTMLBuilder
from sphinx.util.osutil import EEXIST


# (Fragment) templates from which the metainfo files content.opf, toc.ncx,
# mimetype, and META-INF/container.xml are created.
# This template section also defines strings that are embedded in the html
# output but that may be customized by (re-)setting module attributes,
# e.g. from conf.py.

_mimetype_template = 'application/epub+zip' # no EOL!

_container_template = u'''\
<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0"
      xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf"
        media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
'''

_toc_template = u'''\
<?xml version="1.0"?>
<ncx version="2005-1" xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <head>
    <meta name="dtb:uid" content="%(uid)s"/>
    <meta name="dtb:depth" content="%(level)d"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle>
    <text>%(title)s</text>
  </docTitle>
  <navMap>
%(navpoints)s
  </navMap>
</ncx>
'''

_navpoint_template = u'''\
%(indent)s  <navPoint id="%(navpoint)s" playOrder="%(playorder)d">
%(indent)s    <navLabel>
%(indent)s      <text>%(text)s</text>
%(indent)s    </navLabel>
%(indent)s    <content src="%(refuri)s" />
%(indent)s  </navPoint>'''

_navpoint_indent = '  '
_navPoint_template = 'navPoint%d'

_content_template = u'''\
<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0"
      unique-identifier="%(uid)s">
  <metadata xmlns:opf="http://www.idpf.org/2007/opf"
        xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:language>%(lang)s</dc:language>
    <dc:title>%(title)s</dc:title>
    <dc:creator opf:role="aut">%(author)s</dc:creator>
    <dc:publisher>%(publisher)s</dc:publisher>
    <dc:rights>%(copyright)s</dc:rights>
    <dc:identifier id="%(uid)s" opf:scheme="%(scheme)s">%(id)s</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml" />
%(files)s
  </manifest>
  <spine toc="ncx">
%(spine)s
  </spine>
</package>
'''

_file_template = u'''\
    <item id="%(id)s"
          href="%(href)s"
          media-type="%(media_type)s" />'''

_spine_template = u'''\
    <itemref idref="%(idref)s" />'''

_toctree_template = u'toctree-l%d'

_link_target_template = u' [%(uri)s]'

_css_link_target_class = u'link-target'

_media_types = {
    '.html': 'application/xhtml+xml',
    '.css': 'text/css',
    '.png': 'image/png',
    '.gif': 'image/gif',
    '.svg': 'image/svg+xml',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.otf': 'application/x-font-otf',
    '.ttf': 'application/x-font-ttf',
}


# The transform to show link targets

class VisibleLinksTransform(Transform):
    """
    Add the link target of referances to the text, unless it is already
    present in the description.
    """

    # This transform must run after the references transforms
    default_priority = 680

    def apply(self):
        for ref in self.document.traverse(nodes.reference):
            uri = ref.get('refuri', '')
            if ( uri.startswith('http:') or uri.startswith('https:') or \
                    uri.startswith('ftp:') ) and uri not in ref.astext():
                uri = _link_target_template % {'uri': uri}
                if uri:
                    idx = ref.parent.index(ref) + 1
                    link = nodes.inline(uri, uri)
                    link['classes'].append(_css_link_target_class)
                    ref.parent.insert(idx, link)


# The epub publisher

class EpubBuilder(StandaloneHTMLBuilder):
    """Builder that outputs epub files.

    It creates the metainfo files container.opf, toc.ncx, mimetype, and
    META-INF/container.xml.  Afterwards, all necessary files are zipped to an
    epub file.
    """
    name = 'epub'

    # don't copy the reST source
    copysource = False
    supported_image_types = ['image/svg+xml', 'image/png', 'image/gif',
                             'image/jpeg']

    # don't add links
    add_permalinks = False
    # don't add sidebar etc.
    embedded = True

    def init(self):
        StandaloneHTMLBuilder.init(self)
        # the output files for epub must be .html only
        self.out_suffix = '.html'
        self.playorder = 0
        self.app.add_transform(VisibleLinksTransform)

    def get_theme_config(self):
        return self.config.epub_theme, {}

    # generic support functions
    def make_id(self, name):
        """Replace all characters not allowed for (X)HTML ids."""
        return name.replace('/', '_').replace(' ', '')

    def esc(self, name):
        """Replace all characters not allowed in text an attribute values."""
        # Like cgi.escape, but also replace apostrophe
        name = name.replace('&', '&amp;')
        name = name.replace('<', '&lt;')
        name = name.replace('>', '&gt;')
        name = name.replace('"', '&quot;')
        name = name.replace('\'', '&apos;')
        return name

    def get_refnodes(self, doctree, result):
        """Collect section titles, their depth in the toc and the refuri."""
        # XXX: is there a better way than checking the attribute
        # toctree-l[1-8] on the parent node?
        if isinstance(doctree, nodes.reference):
            classes = doctree.parent.attributes['classes']
            level = 1
            for l in range(8, 0, -1): # or range(1, 8)?
                if (_toctree_template % l) in classes:
                    level = l
            result.append({
                'level': level,
                'refuri': self.esc(doctree['refuri']),
                'text': self.esc(doctree.astext())
            })
        else:
            for elem in doctree.children:
                result = self.get_refnodes(elem, result)
        return result

    def get_toc(self):
        """Get the total table of contents, containg the master_doc
        and pre and post files not managed by sphinx.
        """
        doctree = self.env.get_and_resolve_doctree(self.config.master_doc,
            self, prune_toctrees=False)
        self.refnodes = self.get_refnodes(doctree, [])
        self.refnodes.insert(0, {
            'level': 1,
            'refuri': self.esc(self.config.master_doc + '.html'),
            'text': self.esc(self.env.titles[self.config.master_doc].astext())
        })
        for file, text in reversed(self.config.epub_pre_files):
            self.refnodes.insert(0, {
                'level': 1,
                'refuri': self.esc(file + '.html'),
                'text': self.esc(text)
            })
        for file, text in self.config.epub_post_files:
            self.refnodes.append({
                'level': 1,
                'refuri': self.esc(file + '.html'),
                'text': self.esc(text)
            })


    # Finish by building the epub file
    def handle_finish(self):
        """Create the metainfo files and finally the epub."""
        self.get_toc()
        self.build_mimetype(self.outdir, 'mimetype')
        self.build_container(self.outdir, 'META-INF/container.xml')
        self.build_content(self.outdir, 'content.opf')
        self.build_toc(self.outdir, 'toc.ncx')
        self.build_epub(self.outdir, self.config.epub_basename + '.epub')

    def build_mimetype(self, outdir, outname):
        """Write the metainfo file mimetype."""
        self.info('writing %s file...' % outname)
        f = codecs.open(path.join(outdir, outname), 'w', 'utf-8')
        try:
            f.write(_mimetype_template)
        finally:
            f.close()

    def build_container(self, outdir, outname):
        """Write the metainfo file META-INF/cointainer.xml."""
        self.info('writing %s file...' % outname)
        fn = path.join(outdir, outname)
        try:
            os.mkdir(path.dirname(fn))
        except OSError, err:
            if err.errno != EEXIST:
                raise
        f = codecs.open(path.join(outdir, outname), 'w', 'utf-8')
        try:
            f.write(_container_template)
        finally:
            f.close()

    def content_metadata(self, files, spine):
        """Create a dictionary with all metadata for the content.opf
        file properly escaped.
        """
        metadata = {}
        metadata['title'] = self.esc(self.config.epub_title)
        metadata['author'] = self.esc(self.config.epub_author)
        metadata['uid'] = self.esc(self.config.epub_uid)
        metadata['lang'] = self.esc(self.config.epub_language)
        metadata['publisher'] = self.esc(self.config.epub_publisher)
        metadata['copyright'] = self.esc(self.config.epub_copyright)
        metadata['scheme'] = self.esc(self.config.epub_scheme)
        metadata['id'] = self.esc(self.config.epub_identifier)
        metadata['files'] = files
        metadata['spine'] = spine
        return metadata

    def build_content(self, outdir, outname):
        """Write the metainfo file content.opf It contains bibliographic data,
        a file list and the spine (the reading order).
        """
        self.info('writing %s file...' % outname)

        # files
        if not outdir.endswith(os.sep):
            outdir += os.sep
        olen = len(outdir)
        projectfiles = []
        self.files = []
        self.ignored_files = ['.buildinfo',
            'mimetype', 'content.opf', 'toc.ncx', 'META-INF/container.xml',
            self.config.epub_basename + '.epub'] + \
            self.config.epub_exclude_files
        for root, dirs, files in os.walk(outdir):
            for fn in files:
                filename = path.join(root, fn)[olen:]
                if filename in self.ignored_files:
                    continue
                ext = path.splitext(filename)[-1]
                if ext not in _media_types:
                    self.warn('unknown mimetype for %s, ignoring' % filename)
                    continue
                projectfiles.append(_file_template % {
                    'href': self.esc(filename),
                    'id': self.esc(self.make_id(filename)),
                    'media_type': self.esc(_media_types[ext])
                })
                self.files.append(filename)
        projectfiles = '\n'.join(projectfiles)

        # spine
        spine = []
        for item in self.refnodes:
            if '#' in item['refuri']:
                continue
            if item['refuri'] in self.ignored_files:
                continue
            spine.append(_spine_template % {
                'idref': self.esc(self.make_id(item['refuri']))
            })
        spine = '\n'.join(spine)

        # write the project file
        f = codecs.open(path.join(outdir, outname), 'w', 'utf-8')
        try:
            f.write(_content_template % \
                self.content_metadata(projectfiles, spine))
        finally:
            f.close()

    def new_navpoint(self, node, level, incr=True):
        """Create a new entry in the toc from the node at given level."""
        # XXX Modifies the node
        if incr:
            self.playorder += 1
        node['indent'] = _navpoint_indent * level
        node['navpoint'] = self.esc(_navPoint_template % self.playorder)
        node['playorder'] = self.playorder
        return _navpoint_template % node

    def insert_subnav(self, node, subnav):
        """Insert nested navpoints for given node.
        The node and subnav are already rendered to text.
        """
        nlist = node.rsplit('\n', 1)
        nlist.insert(-1, subnav)
        return '\n'.join(nlist)

    def build_navpoints(self, nodes):
        """Create the toc navigation structure.

        Subelements of a node are nested inside the navpoint.
        For nested nodes the parent node is reinserted in the subnav.
        """
        navstack = []
        navlist = []
        level = 1
        lastnode = None
        for node in nodes:
            file = node['refuri'].split('#')[0]
            if file in self.ignored_files:
                continue
            if node['level'] > self.config.epub_tocdepth:
                continue
            if node['level'] == level:
                navlist.append(self.new_navpoint(node, level))
            elif node['level'] == level + 1:
                navstack.append(navlist)
                navlist = []
                level += 1
                if lastnode:
                    # Insert starting point in subtoc with same playOrder
                    navlist.append(self.new_navpoint(lastnode, level, False))
                navlist.append(self.new_navpoint(node, level))
            else:
                while node['level'] < level:
                    subnav = '\n'.join(navlist)
                    navlist = navstack.pop()
                    navlist[-1] = self.insert_subnav(navlist[-1], subnav)
                    level -= 1
                navlist.append(self.new_navpoint(node, level))
            lastnode = node
        while level != 1:
            subnav = '\n'.join(navlist)
            navlist = navstack.pop()
            navlist[-1] = self.insert_subnav(navlist[-1], subnav)
            level -= 1
        return '\n'.join(navlist)

    def toc_metadata(self, level, navpoints):
        """Create a dictionary with all metadata for the toc.ncx
        file properly escaped.
        """
        metadata = {}
        metadata['uid'] = self.config.epub_uid
        metadata['title'] = self.config.epub_title
        metadata['level'] = level
        metadata['navpoints'] = navpoints
        return metadata

    def build_toc(self, outdir, outname):
        """Write the metainfo file toc.ncx."""
        self.info('writing %s file...' % outname)

        navpoints = self.build_navpoints(self.refnodes)
        level = max(item['level'] for item in self.refnodes)
        level = min(level, self.config.epub_tocdepth)
        f = codecs.open(path.join(outdir, outname), 'w', 'utf-8')
        try:
            f.write(_toc_template % self.toc_metadata(level, navpoints))
        finally:
            f.close()

    def build_epub(self, outdir, outname):
        """Write the epub file.

        It is a zip file with the mimetype file stored uncompressed
        as the first entry.
        """
        self.info('writing %s file...' % outname)
        projectfiles = ['META-INF/container.xml', 'content.opf', 'toc.ncx'] \
            + self.files
        epub = zipfile.ZipFile(path.join(outdir, outname), 'w', \
            zipfile.ZIP_DEFLATED)
        epub.write(path.join(outdir, 'mimetype'), 'mimetype', \
            zipfile.ZIP_STORED)
        for file in projectfiles:
            epub.write(path.join(outdir, file), file, zipfile.ZIP_DEFLATED)
        epub.close()