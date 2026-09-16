"""Exercise the PDF browser fallback in Node without browser dependencies."""

from pathlib import Path
import shutil
import subprocess
import unittest


class PdfViewerFallbackTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_browser_fallback_and_raster_renderer(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/document.js', 'utf8');
const render = source.slice(source.indexOf('  async function _renderPdfPane()'),
                            source.indexOf('  function _buildAnnotation('));
function element(tagName) {
  return {
    tagName, style: {}, children: [], dataset: {},
    appendChild(child) { this.children.push(child); },
    addEventListener() {},
    set innerHTML(value) { this.html = value; this.children = []; },
    get innerHTML() { return this.html; },
  };
}
const pane = element('div');
const controls = [element('button'), element('button'), element('button')];
const context = {
  API_BASE: '', activeDocId: 'document-123',
  docs: new Map([['document-123', {title: 'Test PDF', content: '<!-- pdf_form_source upload_id="source" -->'}]]),
  document: {
    getElementById: id => id === 'doc-pdf-view' ? pane : null,
    createElement: element,
    querySelectorAll: () => controls,
  },
  _wirePdfPaneProximity() {},
  _setPdfDropMode() {},
  _pdfPaneFieldsByDoc: new Map([['document-123', ['stale fields']]]),
  _pdfPaneAnnotationsByDoc: new Map([['document-123', ['stale annotations']]]),
  _pdfLastLineHeight: new Map(),
  _parseAnnotations: () => [],
  _pdfResponseErrorMessage: async () => 'Not allowed',
  _escHtml: text => text,
  fetch: async () => ({ok: true, json: async () => ({renderer: 'browser', pages: []})}),
};
vm.createContext(context);
vm.runInContext(render, context);
(async () => {
  await context._renderPdfPane();
  const viewer = pane.children[0];
  const [notice, frame] = viewer.children;
  assert.equal(frame.tagName, 'iframe');
  assert.equal(frame.src, '/api/document/document-123/render-pdf?source=1');
  assert.equal(frame.title, 'Test PDF');
  assert.equal(notice.children[0].href, frame.src);
  assert.match(notice.textContent, /Saved field edits and annotations are not shown/);
  assert.ok(controls.every(el => el.style.display === 'none'));
  assert.equal(context._pdfPaneFieldsByDoc.has('document-123'), false);
  assert.equal(context._pdfPaneAnnotationsByDoc.has('document-123'), false);

  // Restoring optional rendering must restore controls and page images.
  context.fetch = async () => ({ok: true, json: async () => ({
    pages: [{page: 1, width: 100, height: 200, fields: []}],
  })});
  await context._renderPdfPane();
  assert.equal(pane.children[0].children[0].tagName, 'img');
  assert.equal(pane.children[0].children[0].src, '/api/document/document-123/page/1.png');
  assert.ok(controls.every(el => el.style.display === ''));

  // A request for a tab that was closed must not replace the new tab's view.
  context.fetch = async () => {
    context.activeDocId = 'different-document';
    pane.innerHTML = 'new document view';
    return {ok: false};
  };
  await context._renderPdfPane();
  assert.equal(pane.innerHTML, 'new document view');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
