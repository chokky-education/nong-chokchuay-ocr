document.addEventListener('DOMContentLoaded', () => {
  // Elements
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('file-input');
  const btnSelectFile = document.getElementById('btn-select-file');
  const fileBadge = document.getElementById('file-badge');
  const viewerPlaceholder = document.getElementById('viewer-placeholder');
  const docImage = document.getElementById('doc-image');
  const boxesLayer = document.getElementById('boxes-layer');
  const loadingOverlay = document.getElementById('loading-overlay');
  const loadingText = document.getElementById('loading-text');
  const toggleBoxes = document.getElementById('toggle-boxes');
  const docDimensions = document.getElementById('doc-dimensions');

  // Stats
  const statTime = document.getElementById('stat-time');
  const statLines = document.getElementById('stat-lines');
  const statConf = document.getElementById('stat-conf');
  const statDbStatus = document.getElementById('stat-db-status');
  const footerDocId = document.getElementById('footer-doc-id');

  // Result Tabs
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabContents = document.querySelectorAll('.tab-content');
  const ocrTextResult = document.getElementById('ocr-text-result');
  const ocrMarkdownResult = document.getElementById('ocr-markdown-result');
  const ocrJsonResult = document.getElementById('ocr-json-result');
  const linesTbody = document.getElementById('lines-tbody');
  const charCount = document.getElementById('char-count');

  // PDF Controls
  const pdfControls = document.getElementById('pdf-controls');
  const btnPrevPage = document.getElementById('btn-prev-page');
  const btnNextPage = document.getElementById('btn-next-page');
  const btnOcrAllPages = document.getElementById('btn-ocr-all-pages');
  const pdfPageIndicator = document.getElementById('pdf-page-indicator');

  // Database Modal Elements
  const btnOpenDb = document.getElementById('btn-open-db');
  const btnCloseDb = document.getElementById('btn-close-db');
  const dbModal = document.getElementById('db-modal');
  const dbCountBadge = document.getElementById('db-count-badge');
  const dbSearchInput = document.getElementById('db-search-input');
  const btnClearSearch = document.getElementById('btn-clear-search');
  const docsList = document.getElementById('docs-list');
  const dbSummaryStats = document.getElementById('db-summary-stats');

  // Actions
  const btnCopy = document.getElementById('btn-copy');
  const btnDownloadTxt = document.getElementById('btn-download-txt');
  const btnDownloadJson = document.getElementById('btn-download-json');
  const toast = document.getElementById('toast');
  const toastMessage = document.getElementById('toast-message');

  // State
  let currentFile = null;
  let currentPdfPage = 1;
  let totalPdfPages = 1;
  let isPdfFile = false;
  let lastOcrData = null;
  let searchDebounceTimeout = null;

  // Initialize DB stats
  refreshDbStats();

  // Tab switching
  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      tabBtns.forEach(b => b.classList.remove('active'));
      tabContents.forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      const targetId = btn.getAttribute('data-tab');
      const targetContent = document.getElementById(targetId);
      if (targetContent) targetContent.classList.add('active');
    });
  });

  // Toggle bounding boxes
  toggleBoxes.addEventListener('change', (e) => {
    boxesLayer.style.display = e.target.checked ? 'block' : 'none';
  });

  // File selection triggers
  btnSelectFile.addEventListener('click', () => fileInput.click());
  dropzone.addEventListener('click', () => fileInput.click());

  fileInput.addEventListener('change', (e) => {
    if (e.target.files && e.target.files[0]) {
      handleFileSelected(e.target.files[0]);
    }
  });

  // Drag & drop
  dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('dragover');
  });

  dropzone.addEventListener('dragleave', () => {
    dropzone.classList.remove('dragover');
  });

  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileSelected(e.dataTransfer.files[0]);
    }
  });

  // Sample buttons
  document.querySelectorAll('.btn-sample').forEach(btn => {
    btn.addEventListener('click', async () => {
      const sampleId = btn.getAttribute('data-sample');
      try {
        showLoading(`กำลังโหลดเอกสารตัวอย่าง...`);
        const resp = await fetch(`/api/sample/${sampleId}`);
        if (!resp.ok) throw new Error('ไม่สามารถโหลดเอกสารตัวอย่างได้');
        const blob = await resp.blob();
        const file = new File([blob], sampleId, { type: blob.type || 'image/png' });
        handleFileSelected(file);
      } catch (err) {
        hideLoading();
        showToast('เกิดข้อผิดพลาด: ' + err.message);
      }
    });
  });

  function handleFileSelected(file) {
    currentFile = file;
    currentPdfPage = 1;
    isPdfFile = file.name.toLowerCase().endsWith('.pdf') || file.type === 'application/pdf';

    fileBadge.textContent = file.name;
    fileBadge.style.display = 'inline-flex';

    runOcr(file, 1, false);
  }

  // Run OCR API
  async function runOcr(file, pageNum = 1, processAll = false) {
    showLoading(processAll ? `กำลังอ่านเอกสารทุกหน้า (Batch)...` : `กำลังอ่านข้อความด้วย OpenThai 2.0 OCR...`);

    const formData = new FormData();
    formData.append('file', file);
    formData.append('page', pageNum);
    formData.append('process_all_pages', processAll);
    formData.append('save_to_db', 'true');

    try {
      const resp = await fetch('/api/ocr', {
        method: 'POST',
        body: formData
      });

      const data = await resp.json();
      if (!resp.ok || !data.success) {
        throw new Error(data.error || 'การประมวลผลล้มเหลว');
      }

      lastOcrData = data;
      renderResults(data);
      refreshDbStats();
      showToast('อ่านข้อความและบันทึกลงฐานข้อมูลสำเร็จ!');
    } catch (err) {
      showToast('เกิดข้อผิดพลาด: ' + err.message);
      console.error(err);
    } finally {
      hideLoading();
    }
  }

  // Render OCR Results
  function renderResults(data) {
    statTime.textContent = (data.stats.duration_ms / 1000).toFixed(2) + 's';
    statLines.textContent = data.stats.total_lines + ' บรรทัด';
    statConf.textContent = (data.stats.avg_confidence * 100).toFixed(1) + '%';
    statDbStatus.textContent = data.doc_id ? 'บันทึกแล้ว' : 'ชั่วคราว';
    footerDocId.textContent = data.doc_id ? `ID: ${data.doc_id}` : 'OpenThai 2.0 OCR Text';

    ocrTextResult.value = data.text;
    updateCharCount(data.text);

    let md = `# ผลลัพธ์ OCR จาก OpenThai 2.0\n\n`;
    md += `**ไฟล์:** \`${data.filename}\` | **เวลา:** \`${(data.stats.duration_ms/1000).toFixed(2)}s\` | **ความแม่นยำ:** \`${(data.stats.avg_confidence*100).toFixed(1)}%\`\n\n---\n\n`;
    md += data.text;
    ocrMarkdownResult.value = md;

    ocrJsonResult.value = JSON.stringify(data, null, 2);

    if (data.is_pdf && data.total_pages > 1) {
      isPdfFile = true;
      totalPdfPages = data.total_pages;
      currentPdfPage = data.current_page || 1;
      pdfControls.style.display = 'flex';
      pdfPageIndicator.textContent = `หน้า ${currentPdfPage} / ${totalPdfPages}`;
      btnPrevPage.disabled = currentPdfPage <= 1;
      btnNextPage.disabled = currentPdfPage >= totalPdfPages;
    } else {
      pdfControls.style.display = 'none';
    }

    if (data.image_preview) {
      viewerPlaceholder.style.display = 'none';
      docImage.style.display = 'block';
      docImage.src = data.image_preview;

      docImage.onload = () => {
        docDimensions.textContent = `ขนาดภาพ: ${data.image_width || docImage.naturalWidth} × ${data.image_height || docImage.naturalHeight} px`;
        renderBoundingBoxes(data.lines, data.image_width || docImage.naturalWidth, data.image_height || docImage.naturalHeight);
      };
    }

    renderLinesTable(data.lines);
  }

  // Render Bounding Boxes on Overlay
  function renderBoundingBoxes(lines, origW, origH) {
    boxesLayer.innerHTML = '';
    if (!lines || lines.length === 0 || !origW || !origH) return;

    lines.forEach((line, index) => {
      if (!line.box || line.box.length < 4) return;

      const xs = line.box.map(pt => pt[0]);
      const ys = line.box.map(pt => pt[1]);
      const minX = Math.min(...xs);
      const maxX = Math.max(...xs);
      const minY = Math.min(...ys);
      const maxY = Math.max(...ys);

      const leftPct = (minX / origW) * 100;
      const topPct = (minY / origH) * 100;
      const widthPct = ((maxX - minX) / origW) * 100;
      const heightPct = ((maxY - minY) / origH) * 100;

      const boxElem = document.createElement('div');
      boxElem.className = 'bounding-box';
      boxElem.id = `box-line-${index}`;
      boxElem.style.left = `${leftPct}%`;
      boxElem.style.top = `${topPct}%`;
      boxElem.style.width = `${widthPct}%`;
      boxElem.style.height = `${heightPct}%`;

      const confPct = (line.confidence * 100).toFixed(0);
      boxElem.innerHTML = `
        <div class="bounding-box-tooltip">
          <strong>#${index + 1}</strong> (${confPct}%): ${escapeHtml(line.text)}
        </div>
      `;

      boxElem.addEventListener('mouseenter', () => highlightLine(index));
      boxElem.addEventListener('mouseleave', () => unhighlightLine(index));
      boxElem.addEventListener('click', () => {
        document.querySelector('[data-tab="tab-lines"]').click();
        const row = document.getElementById(`line-row-${index}`);
        if (row) {
          row.scrollIntoView({ behavior: 'smooth', block: 'center' });
          highlightLine(index);
        }
      });

      boxesLayer.appendChild(boxElem);
    });
  }

  // Render Lines Table
  function renderLinesTable(lines) {
    linesTbody.innerHTML = '';
    if (!lines || lines.length === 0) {
      linesTbody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: var(--text-muted); padding: 2rem;">ไม่พบบรรทัดข้อความ</td></tr>';
      return;
    }

    lines.forEach((line, idx) => {
      const tr = document.createElement('tr');
      tr.id = `line-row-${idx}`;

      const confVal = (line.confidence * 100).toFixed(1);
      let confClass = 'conf-high';
      if (line.confidence < 0.8) confClass = 'conf-low';
      else if (line.confidence < 0.95) confClass = 'conf-med';

      tr.innerHTML = `
        <td style="font-weight: 600; color: var(--text-muted);">${idx + 1}</td>
        <td>${escapeHtml(line.text)}</td>
        <td><span class="conf-pill ${confClass}">${confVal}%</span></td>
      `;

      tr.addEventListener('mouseenter', () => highlightLine(idx));
      tr.addEventListener('mouseleave', () => unhighlightLine(idx));

      linesTbody.appendChild(tr);
    });
  }

  function highlightLine(index) {
    const box = document.getElementById(`box-line-${index}`);
    if (box) box.classList.add('active');
    const row = document.getElementById(`line-row-${index}`);
    if (row) row.classList.add('active');
  }

  function unhighlightLine(index) {
    const box = document.getElementById(`box-line-${index}`);
    if (box) box.classList.remove('active');
    const row = document.getElementById(`line-row-${index}`);
    if (row) row.classList.remove('active');
  }

  // Database Management
  btnOpenDb.addEventListener('click', () => {
    dbModal.style.display = 'flex';
    loadDatabaseDocuments();
  });

  btnCloseDb.addEventListener('click', () => {
    dbModal.style.display = 'none';
  });

  dbModal.addEventListener('click', (e) => {
    if (e.target === dbModal) dbModal.style.display = 'none';
  });

  dbSearchInput.addEventListener('input', () => {
    clearTimeout(searchDebounceTimeout);
    searchDebounceTimeout = setTimeout(() => {
      loadDatabaseDocuments(dbSearchInput.value);
    }, 250);
  });

  btnClearSearch.addEventListener('click', () => {
    dbSearchInput.value = '';
    loadDatabaseDocuments();
  });

  async function refreshDbStats() {
    try {
      const resp = await fetch('/api/documents');
      const data = await resp.json();
      if (data.db_stats) {
        dbCountBadge.textContent = data.db_stats.total_documents || 0;
      }
    } catch (e) {
      console.error(e);
    }
  }

  async function loadDatabaseDocuments(query = '') {
    try {
      docsList.innerHTML = '<div style="text-align: center; padding: 2rem; color: var(--text-muted);">กำลังค้นหา...</div>';
      const url = query ? `/api/documents?query=${encodeURIComponent(query)}` : '/api/documents';
      const resp = await fetch(url);
      const data = await resp.json();

      const stats = data.db_stats;
      dbSummaryStats.innerHTML = `คลังเอกสาร: <strong>${stats.total_documents}</strong> ฉบับ | หน้าทั้งหมด: <strong>${stats.total_pages}</strong> หน้า | ตัวอักษรสะสม: <strong>${stats.total_characters.toLocaleString()}</strong> ตัว | ที่จัดเก็บ: <code>${stats.db_path}</code>`;
      dbCountBadge.textContent = stats.total_documents;

      if (!data.documents || data.documents.length === 0) {
        docsList.innerHTML = `
          <div style="text-align: center; padding: 3rem; color: var(--text-muted);">
            <div style="font-size: 2.5rem; margin-bottom: 0.5rem;">🔍</div>
            <p>${query ? `ไม่พบเอกสารที่มีคำว่า "${escapeHtml(query)}"` : 'ยังไม่มีเอกสารในฐานข้อมูล'}</p>
          </div>
        `;
        return;
      }

      docsList.innerHTML = '';
      data.documents.forEach(doc => {
        const card = document.createElement('div');
        card.className = 'doc-card';

        const confPct = (doc.avg_confidence * 100).toFixed(1);
        const timeSec = (doc.duration_ms / 1000).toFixed(2);

        card.innerHTML = `
          <div class="doc-card-info">
            <div class="doc-card-title">
              <span>📄 ${escapeHtml(doc.filename)}</span>
              <span class="badge" style="font-size: 11px;">${doc.file_type.toUpperCase()}</span>
              <span class="badge badge-green" style="font-size: 11px;">ความมั่นใจ ${confPct}%</span>
            </div>
            <div class="doc-card-meta">
              <span>📅 ${doc.created_at}</span>
              <span>⚡ ${timeSec}s</span>
              <span>📝 ${doc.char_count.toLocaleString()} ตัวอักษร</span>
              <span>📑 ${doc.total_pages} หน้า</span>
            </div>
            <div class="doc-card-snippet">
              ${escapeHtml(doc.snippet)}...
            </div>
          </div>
          <div class="doc-card-actions">
            <button class="btn btn-primary btn-sm btn-open-doc" data-id="${doc.id}">🔍 เปิดดู</button>
            <button class="btn btn-secondary btn-sm btn-copy-doc" data-id="${doc.id}">📋 คัดลอก</button>
            <button class="btn btn-danger btn-sm btn-delete-doc" data-id="${doc.id}">🗑️ ลบ</button>
          </div>
        `;

        card.querySelector('.btn-open-doc').addEventListener('click', () => openDocFromDb(doc.id));
        card.querySelector('.btn-copy-doc').addEventListener('click', () => copyDocTextFromDb(doc.id));
        card.querySelector('.btn-delete-doc').addEventListener('click', () => deleteDocFromDb(doc.id));

        docsList.appendChild(card);
      });
    } catch (err) {
      docsList.innerHTML = `<div style="color: red; padding: 1rem;">ข้อผิดพลาด: ${err.message}</div>`;
    }
  }

  async function openDocFromDb(docId) {
    try {
      showLoading('กำลังโหลดเอกสารจากฐานข้อมูล...');
      dbModal.style.display = 'none';
      const resp = await fetch(`/api/documents/${docId}`);
      const data = await resp.json();
      if (!data.success || !data.document) throw new Error('ไม่พบข้อมูลเอกสาร');

      const doc = data.document;
      fileBadge.textContent = doc.filename;
      fileBadge.style.display = 'inline-flex';

      const adaptedData = {
        success: true,
        doc_id: doc.id,
        filename: doc.filename,
        is_pdf: doc.file_type === 'pdf',
        total_pages: doc.total_pages,
        current_page: 1,
        processed_pages_count: doc.total_pages,
        text: doc.text,
        lines: doc.lines || [],
        image_preview: doc.image_preview,
        image_width: 1200,
        image_height: 1600,
        stats: {
          duration_ms: doc.duration_ms,
          avg_ms_per_page: (doc.duration_ms / doc.total_pages),
          total_lines: (doc.lines || []).length,
          avg_confidence: doc.avg_confidence,
          rotation: 0,
          text_angle: 0.0
        }
      };

      lastOcrData = adaptedData;
      renderResults(adaptedData);
      showToast('เปิดเอกสารจากฐานข้อมูลสำเร็จ');
    } catch (err) {
      showToast('เกิดข้อผิดพลาด: ' + err.message);
    } finally {
      hideLoading();
    }
  }

  async function copyDocTextFromDb(docId) {
    try {
      const resp = await fetch(`/api/documents/${docId}`);
      const data = await resp.json();
      if (data.document && data.document.text) {
        await navigator.clipboard.writeText(data.document.text);
        showToast('คัดลอกข้อความสำเร็จเรียบร้อย');
      }
    } catch (e) {
      showToast('ไม่สามารถคัดลอกได้');
    }
  }

  async function deleteDocFromDb(docId) {
    if (!confirm('คุณต้องการลบเอกสารนี้ออกจากฐานข้อมูลหรือไม่?')) return;
    try {
      const resp = await fetch(`/api/documents/${docId}`, { method: 'DELETE' });
      const data = await resp.json();
      if (data.success) {
        showToast('ลบเอกสารสำเร็จ');
        loadDatabaseDocuments(dbSearchInput.value);
        refreshDbStats();
      }
    } catch (e) {
      showToast('ไม่สามารถลบได้');
    }
  }

  // PDF Page Navigation
  btnPrevPage.addEventListener('click', () => {
    if (currentFile && currentPdfPage > 1) {
      currentPdfPage--;
      runOcr(currentFile, currentPdfPage, false);
    }
  });

  btnNextPage.addEventListener('click', () => {
    if (currentFile && currentPdfPage < totalPdfPages) {
      currentPdfPage++;
      runOcr(currentFile, currentPdfPage, false);
    }
  });

  btnOcrAllPages.addEventListener('click', () => {
    if (currentFile) {
      runOcr(currentFile, 1, true);
    }
  });

  // Action Buttons
  btnCopy.addEventListener('click', async () => {
    const text = ocrTextResult.value;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      showToast('คัดลอกข้อความสำเร็จเรียบร้อย');
    } catch (err) {
      showToast('ไม่สามารถคัดลอกได้');
    }
  });

  btnDownloadTxt.addEventListener('click', () => {
    const text = ocrTextResult.value;
    if (!text) return;
    downloadFile(text, 'opentha-ocr-output.txt', 'text/plain');
    showToast('ดาวน์โหลด .txt สำเร็จ');
  });

  btnDownloadJson.addEventListener('click', () => {
    if (!lastOcrData) return;
    downloadFile(JSON.stringify(lastOcrData, null, 2), 'opentha-ocr-output.json', 'application/json');
    showToast('ดาวน์โหลด .json สำเร็จ');
  });

  ocrTextResult.addEventListener('input', () => {
    updateCharCount(ocrTextResult.value);
  });

  function updateCharCount(text) {
    const chars = text.length;
    const words = text.trim() ? text.trim().split(/\s+/).length : 0;
    charCount.textContent = `${chars.toLocaleString()} ตัวอักษร | ${words.toLocaleString()} คำ`;
  }

  function downloadFile(content, filename, type) {
    const blob = new Blob([content], { type: `${type};charset=utf-8;` });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  function showLoading(msg) {
    loadingText.textContent = msg;
    loadingOverlay.style.display = 'flex';
  }

  function hideLoading() {
    loadingOverlay.style.display = 'none';
  }

  function showToast(msg) {
    toastMessage.textContent = msg;
    toast.classList.add('show');
    setTimeout(() => {
      toast.classList.remove('show');
    }, 2800);
  }

  function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;')
              .replace(/</g, '&lt;')
              .replace(/>/g, '&gt;')
              .replace(/"/g, '&quot;')
              .replace(/'/g, '&#039;');
  }

  // Preload sample on start
  setTimeout(() => {
    const firstSampleBtn = document.querySelector('.btn-sample');
    if (firstSampleBtn) firstSampleBtn.click();
  }, 300);
});
