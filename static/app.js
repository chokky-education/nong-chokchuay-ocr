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

  // Metadata & Vault filter elements
  const btnScanInbox = document.getElementById('btn-scan-inbox');
  const inboxCountBadge = document.getElementById('inbox-count-badge');
  const btnOpenExpenses = document.getElementById('btn-open-expenses');
  const btnExtractExpense = document.getElementById('btn-extract-expense');
  const expenseBanner = document.getElementById('expense-banner');
  const expenseBannerText = document.getElementById('expense-banner-text');
  const btnViewInExpenses = document.getElementById('btn-view-in-expenses');
  const expenseModal = document.getElementById('expense-modal');
  const btnCloseExpenses = document.getElementById('btn-close-expenses');
  const expenseMonthFilter = document.getElementById('expense-month-filter');
  const expenseCategoryFilter = document.getElementById('expense-category-filter');
  const btnClearExpenseFilters = document.getElementById('btn-clear-expense-filters');
  const btnExportExpensesCsv = document.getElementById('btn-export-expenses-csv');
  const expensesTbody = document.getElementById('expenses-tbody');
  const expKpiCount = document.getElementById('exp-kpi-count');
  const expKpiSubtotal = document.getElementById('exp-kpi-subtotal');
  const expKpiVat = document.getElementById('exp-kpi-vat');
  const expKpiTotal = document.getElementById('exp-kpi-total');
  const docCategoryInput = document.getElementById('doc-category-input');
  const docTagsInput = document.getElementById('doc-tags-input');
  const docDateInput = document.getElementById('doc-date-input');
  const btnSaveDoc = document.getElementById('btn-save-doc');
  const btnDownloadSource = document.getElementById('btn-download-source');

  // Database Modal Elements
  const btnOpenDb = document.getElementById('btn-open-db');
  const btnCloseDb = document.getElementById('btn-close-db');
  const dbModal = document.getElementById('db-modal');
  const dbCountBadge = document.getElementById('db-count-badge');
  const dbSearchInput = document.getElementById('db-search-input');
  const dbCategoryFilter = document.getElementById('db-category-filter');
  const dbTagFilter = document.getElementById('db-tag-filter');
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
  let currentDocId = null;
  let currentPdfPage = 1;
  let totalPdfPages = 1;
  let isPdfFile = false;
  let lastOcrData = null;
  let searchDebounceTimeout = null;

  // Initialize DB & Inbox status
  refreshDbStats();
  refreshInboxStatus();
  setInterval(() => {
    refreshInboxStatus();
    refreshDbStats();
  }, 8000);

  async function refreshInboxStatus() {
    try {
      const resp = await fetch('/api/watcher/status');
      const data = await resp.json();
      if (typeof data.pending_count === 'number') {
        inboxCountBadge.textContent = data.pending_count;
      }
      if (data.inbox_dir) {
        btnScanInbox.title = `โฟลเดอร์ Inbox: ${data.inbox_dir} (ล้มเหลว ${data.failed_count || 0})`;
      }
    } catch (e) {
      console.error(e);
    }
  }

  btnScanInbox.addEventListener('click', async () => {
    try {
      btnScanInbox.disabled = true;
      const resp = await fetch('/api/watcher/scan', { method: 'POST' });
      const data = await resp.json();
      await refreshInboxStatus();
      await refreshDbStats();
      showToast(`สแกน Inbox เสร็จ: สำเร็จ ${data.processed || 0} ไฟล์` + (data.failed ? `, ล้มเหลว ${data.failed} ไฟล์` : ''));
    } catch (e) {
      showToast('สแกน Inbox ไม่สำเร็จ: ' + e.message);
    } finally {
      btnScanInbox.disabled = false;
    }
  });

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
    formData.append('category', docCategoryInput.value || 'general');
    formData.append('tags', docTagsInput.value || '');
    formData.append('doc_date', docDateInput.value || '');

    try {
      const resp = await fetch('/api/ocr', {
        method: 'POST',
        body: formData
      });

      const data = await resp.json();
      if (!resp.ok || !data.success) {
        throw new Error(data.error || data.detail || 'การประมวลผลล้มเหลว');
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
    currentDocId = data.doc_id || null;
    btnSaveDoc.disabled = !currentDocId;
    btnExtractExpense.disabled = !currentDocId;

    if (currentDocId && data.has_source_file) {
      btnDownloadSource.href = `/api/documents/${currentDocId}/file`;
      btnDownloadSource.style.display = 'inline-flex';
    } else {
      btnDownloadSource.style.display = 'none';
    }

    renderLinkedExpenseBanner(data.expense || null);

    docCategoryInput.value = data.category || 'general';
    docTagsInput.value = Array.isArray(data.tags) ? data.tags.join(', ') : (data.tags || '');
    docDateInput.value = data.doc_date || '';

    statTime.textContent = (data.stats.duration_ms / 1000).toFixed(2) + 's';
    statLines.textContent = data.stats.total_lines + ' บรรทัด';
    statConf.textContent = (data.stats.avg_confidence * 100).toFixed(1) + '%';
    statDbStatus.textContent = currentDocId ? 'บันทึกแล้ว' : 'ชั่วคราว';
    footerDocId.textContent = currentDocId ? `ID: ${currentDocId}` : 'OpenThai 2.0 OCR Text';

    ocrTextResult.value = data.text || '';
    updateCharCount(ocrTextResult.value);

    let md = `# ผลลัพธ์ OCR จาก OpenThai 2.0\n\n`;
    md += `**ไฟล์:** \`${data.filename}\` | **เวลา:** \`${(data.stats.duration_ms/1000).toFixed(2)}s\` | **ความแม่นยำ:** \`${(data.stats.avg_confidence*100).toFixed(1)}%\`\n\n---\n\n`;
    md += (data.text || '');
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
        const boxW = data.image_width || docImage.naturalWidth;
        const boxH = data.image_height || docImage.naturalHeight;
        docDimensions.textContent = `ขนาดภาพ: ${boxW} × ${boxH} px`;
        renderBoundingBoxes(data.lines, boxW, boxH);
      };
    } else {
      docImage.style.display = 'none';
      viewerPlaceholder.style.display = 'block';
      boxesLayer.innerHTML = '';
      docDimensions.textContent = '';
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

  // Save edited OCR text & metadata to SQLite + re-index FTS5
  btnSaveDoc.addEventListener('click', async () => {
    if (!currentDocId) return;
    const tags = (docTagsInput.value || '')
      .split(',')
      .map(t => t.trim())
      .filter(Boolean);
    try {
      btnSaveDoc.disabled = true;
      const resp = await fetch(`/api/documents/${currentDocId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: ocrTextResult.value,
          category: docCategoryInput.value || 'general',
          tags: tags,
          doc_date: docDateInput.value || ''
        })
      });
      const data = await resp.json();
      if (!resp.ok || !data.success) throw new Error(data.detail || 'บันทึกไม่สำเร็จ');
      if (lastOcrData) {
        lastOcrData.text = data.document.text;
        lastOcrData.category = data.document.category;
        lastOcrData.tags = data.document.tags;
        lastOcrData.doc_date = data.document.doc_date;
        ocrJsonResult.value = JSON.stringify(lastOcrData, null, 2);
      }
      statDbStatus.textContent = 'อัปเดตแล้ว';
      showToast('บันทึกข้อความและอัปเดตดัชนีค้นหาสำเร็จ!');
    } catch (err) {
      showToast('เกิดข้อผิดพลาด: ' + err.message);
    } finally {
      btnSaveDoc.disabled = !currentDocId;
    }
  });

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

  function triggerVaultReload() {
    clearTimeout(searchDebounceTimeout);
    searchDebounceTimeout = setTimeout(() => {
      loadDatabaseDocuments();
    }, 220);
  }

  dbSearchInput.addEventListener('input', triggerVaultReload);
  dbCategoryFilter.addEventListener('change', () => loadDatabaseDocuments());
  dbTagFilter.addEventListener('input', triggerVaultReload);

  btnClearSearch.addEventListener('click', () => {
    dbSearchInput.value = '';
    dbCategoryFilter.value = '';
    dbTagFilter.value = '';
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

  async function loadDatabaseDocuments() {
    const query = (dbSearchInput.value || '').trim();
    const category = (dbCategoryFilter.value || '').trim();
    const tag = (dbTagFilter.value || '').trim();
    try {
      docsList.innerHTML = '<div style="text-align: center; padding: 2rem; color: var(--text-muted);">กำลังค้นหา...</div>';
      const params = new URLSearchParams();
      if (query) params.set('query', query);
      if (category) params.set('category', category);
      if (tag) params.set('tag', tag);
      const url = params.toString() ? `/api/documents?${params.toString()}` : '/api/documents';
      const resp = await fetch(url);
      const data = await resp.json();

      const stats = data.db_stats;
      dbSummaryStats.innerHTML = `คลังเอกสาร: <strong>${stats.total_documents}</strong> ฉบับ | หน้าทั้งหมด: <strong>${stats.total_pages}</strong> หน้า | ตัวอักษรสะสม: <strong>${stats.total_characters.toLocaleString()}</strong> ตัว | ที่จัดเก็บ: <code>${stats.db_path}</code>`;
      dbCountBadge.textContent = stats.total_documents;

      if (!data.documents || data.documents.length === 0) {
        docsList.innerHTML = `
          <div style="text-align: center; padding: 3rem; color: var(--text-muted);">
            <div style="font-size: 2.5rem; margin-bottom: 0.5rem;">🔍</div>
            <p>${query ? `ไม่พบเอกสารที่มีคำว่า "${escapeHtml(query)}"` : 'ไม่พบเอกสารที่ตรงกับเงื่อนไข'}</p>
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
        const channelLabel = doc.ingestion_channel === 'watch_folder' ? '📥 Inbox' : '🌐 Web';
        const tagsHtml = (doc.tags || []).map(t => `<span class="tag-pill">#${escapeHtml(t)}</span>`).join(' ');
        const downloadBtn = doc.has_source_file
          ? `<a href="/api/documents/${doc.id}/file" download class="btn btn-secondary btn-sm" style="text-decoration: none; justify-content: center;">📎 ต้นฉบับ</a>`
          : '';

        card.innerHTML = `
          <div class="doc-card-info">
            <div class="doc-card-title">
              <span>📄 ${escapeHtml(doc.filename)}</span>
              <span class="badge" style="font-size: 11px;">${doc.file_type.toUpperCase()}</span>
              <span class="badge" style="font-size: 11px;">${escapeHtml(doc.category || 'general')}</span>
              <span class="badge" style="font-size: 11px;">${channelLabel}</span>
              <span class="badge badge-green" style="font-size: 11px;">ความมั่นใจ ${confPct}%</span>
            </div>
            <div class="doc-card-meta">
              <span>📅 ${doc.doc_date || doc.created_at}</span>
              <span>⚡ ${timeSec}s</span>
              <span>📝 ${doc.char_count.toLocaleString()} ตัวอักษร</span>
              <span>📑 ${doc.total_pages} หน้า</span>
              ${tagsHtml}
            </div>
            <div class="doc-card-snippet">
              ${escapeHtml(doc.snippet)}...
            </div>
          </div>
          <div class="doc-card-actions">
            <button class="btn btn-primary btn-sm btn-open-doc" data-id="${doc.id}">🔍 เปิดดู</button>
            ${downloadBtn}
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
        image_width: doc.image_width || 0,
        image_height: doc.image_height || 0,
        has_source_file: Boolean(doc.has_source_file),
        category: doc.category || 'general',
        tags: doc.tags || [],
        ingestion_channel: doc.ingestion_channel || 'web_upload',
        doc_date: doc.doc_date || '',
        expense: doc.expense || null,
        stats: {
          duration_ms: doc.duration_ms || 0,
          avg_ms_per_page: ((doc.duration_ms || 0) / Math.max(1, doc.total_pages || 1)),
          total_lines: (doc.lines || []).length,
          avg_confidence: doc.avg_confidence || 1.0,
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
        if (currentDocId === docId) {
          currentDocId = null;
          btnSaveDoc.disabled = true;
          btnExtractExpense.disabled = true;
          btnDownloadSource.style.display = 'none';
          renderLinkedExpenseBanner(null);
        }
        loadDatabaseDocuments();
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

  function renderLinkedExpenseBanner(expense) {
    if (!expense) {
      expenseBanner.style.display = 'none';
      return;
    }
    expenseBanner.style.display = 'flex';
    const dt = expense.expense_date || 'ไม่ระบุวันที่';
    const merchant = expense.merchant || 'ไม่ระบุร้านค้า';
    const total = Number(expense.total_amount || 0).toFixed(2);
    const vat = Number(expense.vat_amount || 0).toFixed(2);
    expenseBannerText.textContent = `🧾 รายจ่าย (${expense.extraction_method}): ${merchant} | 📅 ${dt} | VAT ${vat} | รวม ${total} ${expense.currency || 'THB'}`;
  }

  btnExtractExpense.addEventListener('click', async () => {
    if (!currentDocId) return;
    try {
      btnExtractExpense.disabled = true;
      const resp = await fetch(`/api/documents/${currentDocId}/extract`, { method: 'POST' });
      const data = await resp.json();
      if (!resp.ok || !data.success) throw new Error(data.detail || 'สกัดข้อมูลไม่สำเร็จ');
      renderLinkedExpenseBanner(data.expense);
      showToast(`สกัดข้อมูลใบเสร็จสำเร็จ (${data.expense.extraction_method})!`);
    } catch (e) {
      showToast('เกิดข้อผิดพลาด: ' + e.message);
    } finally {
      btnExtractExpense.disabled = !currentDocId;
    }
  });

  btnViewInExpenses.addEventListener('click', () => {
    expenseModal.style.display = 'flex';
    loadExpenses();
  });

  btnOpenExpenses.addEventListener('click', () => {
    expenseModal.style.display = 'flex';
    loadExpenses();
  });

  btnCloseExpenses.addEventListener('click', () => {
    expenseModal.style.display = 'none';
  });

  expenseModal.addEventListener('click', (e) => {
    if (e.target === expenseModal) expenseModal.style.display = 'none';
  });

  expenseMonthFilter.addEventListener('change', () => loadExpenses());
  expenseCategoryFilter.addEventListener('change', () => loadExpenses());
  btnClearExpenseFilters.addEventListener('click', () => {
    expenseMonthFilter.value = '';
    expenseCategoryFilter.value = '';
    loadExpenses();
  });

  async function loadExpenses() {
    const month = (expenseMonthFilter.value || '').trim();
    const cat = (expenseCategoryFilter.value || '').trim();
    const params = new URLSearchParams();
    if (month) params.set('month', month);
    if (cat) params.set('expense_category', cat);

    const csvParams = params.toString();
    btnExportExpensesCsv.href = csvParams ? `/api/expenses/export.csv?${csvParams}` : '/api/expenses/export.csv';

    try {
      const url = csvParams ? `/api/expenses?${csvParams}` : '/api/expenses';
      const resp = await fetch(url);
      const data = await resp.json();
      const summary = data.summary || { count: 0, subtotal: 0, vat_amount: 0, total_amount: 0 };
      expKpiCount.textContent = summary.count || 0;
      expKpiSubtotal.textContent = Number(summary.subtotal || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      expKpiVat.textContent = Number(summary.vat_amount || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      expKpiTotal.textContent = Number(summary.total_amount || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

      const items = data.expenses || [];
      if (items.length === 0) {
        expensesTbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 2rem;">ยังไม่มีรายการรายจ่ายตามเงื่อนไขที่เลือก</td></tr>';
        return;
      }

      expensesTbody.innerHTML = '';
      items.forEach(item => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><input type="date" class="meta-input exp-date" value="${escapeHtml(item.expense_date)}" style="width: 130px;"></td>
          <td><input type="text" class="meta-input exp-merchant" value="${escapeHtml(item.merchant)}" style="min-width: 150px;"></td>
          <td>
            <select class="meta-input exp-cat" style="width: 120px;">
              <option value="food" ${item.expense_category === 'food' ? 'selected' : ''}>อาหาร/กาแฟ</option>
              <option value="transport" ${item.expense_category === 'transport' ? 'selected' : ''}>เดินทาง/น้ำมัน</option>
              <option value="utilities" ${item.expense_category === 'utilities' ? 'selected' : ''}>สาธารณูปโภค</option>
              <option value="office" ${item.expense_category === 'office' ? 'selected' : ''}>สำนักงาน/ซอฟต์แวร์</option>
              <option value="other" ${item.expense_category === 'other' ? 'selected' : ''}>อื่นๆ</option>
            </select>
          </td>
          <td><input type="number" step="0.01" class="meta-input exp-sub" value="${Number(item.subtotal || 0).toFixed(2)}" style="width: 90px;"></td>
          <td><input type="number" step="0.01" class="meta-input exp-vat" value="${Number(item.vat_amount || 0).toFixed(2)}" style="width: 80px;"></td>
          <td><input type="number" step="0.01" class="meta-input exp-total" value="${Number(item.total_amount || 0).toFixed(2)}" style="width: 95px; font-weight: 600;"></td>
          <td><span class="badge" style="font-size: 11px;">${escapeHtml(item.extraction_method)}</span></td>
          <td style="white-space: nowrap; display: flex; gap: 0.25rem;">
            <button class="btn btn-primary btn-sm btn-save-exp">💾</button>
            <button class="btn btn-secondary btn-sm btn-open-exp-doc">🔍</button>
            <button class="btn btn-danger btn-sm btn-del-exp">🗑️</button>
          </td>
        `;

        tr.querySelector('.btn-save-exp').addEventListener('click', async () => {
          const payload = {
            expense_date: tr.querySelector('.exp-date').value || '',
            merchant: tr.querySelector('.exp-merchant').value || '',
            expense_category: tr.querySelector('.exp-cat').value || 'other',
            subtotal: parseFloat(tr.querySelector('.exp-sub').value || '0') || 0,
            vat_amount: parseFloat(tr.querySelector('.exp-vat').value || '0') || 0,
            total_amount: parseFloat(tr.querySelector('.exp-total').value || '0') || 0,
            currency: item.currency || 'THB'
          };
          const upd = await fetch(`/api/expenses/${item.document_id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });
          if (upd.ok) {
            showToast('บันทึกการแก้ไขรายจ่ายแล้ว');
            loadExpenses();
          }
        });

        tr.querySelector('.btn-open-exp-doc').addEventListener('click', () => {
          expenseModal.style.display = 'none';
          openDocFromDb(item.document_id);
        });

        tr.querySelector('.btn-del-exp').addEventListener('click', async () => {
          if (!confirm('ต้องการลบข้อมูลรายจ่ายนี้ออกจากตารางสรุปหรือไม่? (ตัวเอกสารในคลังจะยังอยู่)')) return;
          const del = await fetch(`/api/expenses/${item.document_id}`, { method: 'DELETE' });
          if (del.ok) {
            showToast('ลบรายการรายจ่ายแล้ว');
            loadExpenses();
          }
        });

        expensesTbody.appendChild(tr);
      });
    } catch (e) {
      expensesTbody.innerHTML = `<tr><td colspan="8" style="color: red; padding: 1rem;">โหลดข้อมูลไม่สำเร็จ: ${escapeHtml(e.message)}</td></tr>`;
    }
  }

  // Preload sample on start
  setTimeout(() => {
    const firstSampleBtn = document.querySelector('.btn-sample');
    if (firstSampleBtn) firstSampleBtn.click();
  }, 300);
});
