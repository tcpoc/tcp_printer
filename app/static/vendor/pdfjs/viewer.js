import * as pdfjsLib from "./build/pdf.mjs";

globalThis.pdfjsLib = pdfjsLib;
await import("./web/pdf_viewer.mjs");

const { EventBus, PDFLinkService, PDFViewer } = globalThis.pdfjsViewer;
const params = new URLSearchParams(window.location.search);
const file = params.get("file");
const loading = document.getElementById("loading");
const error = document.getElementById("error");
const pageNumber = document.getElementById("page-number");
const pageCount = document.getElementById("page-count");

pdfjsLib.GlobalWorkerOptions.workerSrc = "./build/pdf.worker.mjs";

const eventBus = new EventBus();
const linkService = new PDFLinkService({ eventBus });
const pdfViewer = new PDFViewer({
  container: document.getElementById("viewer-container"),
  viewer: document.getElementById("viewer"),
  eventBus,
  linkService,
  annotationMode: pdfjsLib.AnnotationMode.ENABLE_FORMS,
});
linkService.setViewer(pdfViewer);

function showError() {
  loading.hidden = true;
  error.hidden = false;
}

document.getElementById("previous").addEventListener("click", () => { pdfViewer.currentPageNumber -= 1; });
document.getElementById("next").addEventListener("click", () => { pdfViewer.currentPageNumber += 1; });
document.getElementById("zoom-out").addEventListener("click", () => { pdfViewer.currentScale = Math.max(0.5, pdfViewer.currentScale - 0.2); });
document.getElementById("zoom-in").addEventListener("click", () => { pdfViewer.currentScale = Math.min(3, pdfViewer.currentScale + 0.2); });
document.getElementById("zoom-reset").addEventListener("click", () => { pdfViewer.currentScaleValue = "page-width"; });
pageNumber.addEventListener("change", () => { pdfViewer.currentPageNumber = Number(pageNumber.value) || 1; });

eventBus.on("pagesinit", () => { pdfViewer.currentScaleValue = "page-width"; });
eventBus.on("pagechanging", ({ pageNumber: page }) => { pageNumber.value = page; });

if (!file) {
  showError();
} else {
  const task = pdfjsLib.getDocument({
    url: file,
    withCredentials: true,
    cMapUrl: "./cmaps/",
    cMapPacked: true,
    standardFontDataUrl: "./standard_fonts/",
    isEvalSupported: false,
  });
  task.promise.then((document) => {
    loading.hidden = true;
    pageCount.textContent = `/ ${document.numPages}`;
    pageNumber.max = document.numPages;
    pdfViewer.setDocument(document);
    linkService.setDocument(document, null);
  }).catch(showError);
}
