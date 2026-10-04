"""Bounded XML parsing using Qt; DTDs and custom entities are rejected."""
from qgis.PyQt.QtCore import QXmlStreamReader, QByteArray

MAX_BYTES = 8 * 1024 * 1024
MAX_NODES = 50000
MAX_DEPTH = 64


class ParseError(ValueError):
    """Invalid or disallowed XML received from the service."""


class Element:
    __slots__ = ('tag', 'text', 'children')

    def __init__(self, tag):
        self.tag = tag
        self.text = None
        self.children = []

    def __iter__(self):
        return iter(self.children)

    def find(self, path):
        descendant = path.startswith('.//')
        name = path[3:] if descendant else path
        for child in self.children:
            if child.tag == name:
                return child
            if descendant:
                found = child.find(path)
                if found is not None:
                    return found
        return None

    def findtext(self, path, default=None):
        node = self.find(path)
        return default if node is None else (node.text or '')


def fromstring(body):
    if not isinstance(body, (str, bytes, bytearray)):
        raise TypeError('XML body must be text or bytes')
    if len(body) > MAX_BYTES:
        raise ParseError('XML response exceeds the size limit')
    payload = body.encode('utf-8') if isinstance(body, str) else bytes(body)
    if len(payload) > MAX_BYTES:
        raise ParseError('XML response exceeds the size limit')
    # Qt reports DTD before element/entity expansion; reject it immediately.
    reader = QXmlStreamReader(QByteArray(payload))
    reader.setEntityExpansionLimit(0)
    stack, root, count = [], None, 0
    while not reader.atEnd():
        reader.readNext()
        if reader.isDTD() or reader.isEntityReference():
            raise ParseError('DTD and custom entity references are not allowed')
        if reader.isStartElement():
            count += 1
            if count > MAX_NODES or len(stack) >= MAX_DEPTH:
                raise ParseError('XML response exceeds structural limits')
            namespace, name = str(reader.namespaceUri()), str(reader.name())
            node = Element('{' + namespace + '}' + name if namespace else name)
            if stack:
                stack[-1].children.append(node)
            else:
                root = node
            stack.append(node)
        elif reader.isCharacters() and stack and not stack[-1].children:
            node = stack[-1]
            node.text = (node.text or '') + str(reader.text())
        elif reader.isEndElement():
            stack.pop()
    if reader.hasError() or root is None:
        raise ParseError('Invalid XML: ' + str(reader.errorString()))
    return root
