/**
 * GeomatiX Core Engine - Spatial Matrix Controller
 * Author: Asiful Islam Taif
 */

class GeomatiXEngine {
    constructor() {
        this.activeToken = null;
        this.telemetry = null;
        this.rawGridData = null;
        this.normGridData = null;

        this.initDOM();
        this.bindEvents();
    }

    initDOM() {
        this.dropzone = document.getElementById('dropzone');
        this.fileInput = document.getElementById('rasterInput');
        this.spinner = document.getElementById('uploadSpinner');
        this.workspace = document.getElementById('workspace');
        this.normSection = document.getElementById('normSection');
    }

    bindEvents() {
        this.fileInput.addEventListener('change', (e) => this.handleFile(e.target.files[0]));

        this.dropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            this.dropzone.classList.add('drag-over');
        });

        this.dropzone.addEventListener('dragleave', () => this.dropzone.classList.remove('drag-over'));

        this.dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            this.dropzone.classList.remove('drag-over');
            if (e.dataTransfer.files.length) this.handleFile(e.dataTransfer.files[0]);
        });
    }

    async handleFile(file) {
        if (!file) return;

        const formData = new FormData();
        formData.append('raster_file', file);

        this.dropzone.classList.add('hidden');
        this.spinner.classList.remove('hidden');

        try {
            const res = await fetch('/api/v1/spatial/inspect', { method: 'POST', body: formData });
            const data = await res.json();

            if (data.status === 'success') {
                this.activeToken = data.file_token;
                this.telemetry = data;
                this.populateTelemetry();
                await this.buildGrid();
                this.workspace.classList.remove('hidden');
            } else {
                alert(`Inspection Error: ${data.message}`);
                this.dropzone.classList.remove('hidden');
            }
        } catch (err) {
            alert('Server communication failure.');
            this.dropzone.classList.remove('hidden');
        } finally {
            this.spinner.classList.add('hidden');
        }
    }

    populateTelemetry() {
        document.getElementById('metaName').innerText = this.telemetry.original_name;
        document.getElementById('metaFileType').innerText = this.telemetry.file_type;
        document.getElementById('metaDim').innerText = `${this.telemetry.dimensions.width} × ${this.telemetry.dimensions.height} px`;
        document.getElementById('metaBands').innerText = this.telemetry.band_count;
        document.getElementById('metaCRS').innerText = this.telemetry.crs;
        document.getElementById('metaType').innerText = this.telemetry.datatype;

        const selector = document.getElementById('bandSelector');
        selector.innerHTML = '';
        this.telemetry.bands.forEach(b => {
            const opt = document.createElement('option');
            opt.value = b.band_index;
            opt.innerText = `Band ${b.band_index} (Min: ${b.min}, Max: ${b.max})`;
            selector.appendChild(opt);
        });
    }

    async handleBandChange() {
        await this.buildGrid();
        if (!this.normSection.classList.contains('hidden')) {
            await this.calculateNormalizedGridSample();
        }
    }

    async buildGrid() {
        const band = document.getElementById('bandSelector').value || 1;
        const rows = document.getElementById('gridRows').value;
        const cols = document.getElementById('gridCols').value;

        const res = await fetch('/api/v1/spatial/extract-grid', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_token: this.activeToken, band, rows, cols })
        });

        const data = await res.json();
        if (data.status === 'success') {
            this.rawGridData = data.grid;
            this.renderGridUI();
        }
    }

    async normalizeFullRaster() {
        const res = await fetch('/api/v1/spatial/process-normalization', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_token: this.activeToken })
        });

        const data = await res.json();
        if (data.status === 'success') {
            document.getElementById('btnDownloadGeoTIFF').href = `/download/${data.normalized_token}`;
            await this.calculateNormalizedGridSample();
            this.normSection.classList.remove('hidden');
        } else {
            alert(`Normalization failed: ${data.message}`);
        }
    }

    async calculateNormalizedGridSample() {
        let min = Infinity, max = -Infinity;
        this.rawGridData.forEach(row => {
            row.forEach(v => {
                if (v !== null) {
                    if (v < min) min = v;
                    if (v > max) max = v;
                }
            });
        });

        this.normGridData = this.rawGridData.map(row => {
            return row.map(v => (v === null || max === min) ? null : parseFloat(((v - min) / (max - min)).toFixed(3)));
        });

        this.renderGridUI();
    }

    renderGridUI() {
        if (this.rawGridData) {
            this.renderTable(
                'rawGridContainer',
                this.rawGridData,
                document.getElementById('toggleRawLabels').checked,
                document.getElementById('toggleRawValues').checked,
                false
            );
        }

        if (this.normGridData) {
            this.renderTable(
                'normGridContainer',
                this.normGridData,
                true,
                true,
                true
            );
        }
    }

    renderTable(containerId, grid, showHeaders, showValues, applyHeatmap) {
        const container = document.getElementById(containerId);
        let html = '<table class="spatial-table">';

        if (showHeaders) {
            html += '<thead><tr><th></th>';
            for (let c = 0; c < grid[0].length; c++) html += `<th>C${c + 1}</th>`;
            html += '</tr></thead>';
        }

        html += '<tbody>';
        grid.forEach((row, r) => {
            html += '<tr>';
            if (showHeaders) html += `<th>R${r + 1}</th>`;
            row.forEach(val => {
                let style = '';
                if (applyHeatmap && val !== null) {
                    const bg = this.getHeatmapColor(val, document.getElementById('paletteSelect').value);
                    style = `style="background-color: ${bg}; color: #000;"`;
                }
                const displayVal = showValues ? (val !== null ? val : 'ND') : '';
                html += `<td ${style}>${displayVal}</td>`;
            });
            html += '</tr>';
        });

        html += 'tbody></table>';
        container.innerHTML = html;
    }

    getHeatmapColor(val, palette) {
        const h = Math.round(val * 240);
        if (palette === 'grayscale') {
            const g = Math.round(val * 255);
            return `rgb(${g},${g},${g})`;
        }
        return `hsl(${240 - h}, 80%, 50%)`;
    }

    async exportMatrix(mode, format) {
        const grid = mode === 'raw' ? this.rawGridData : this.normGridData;
        const palette = document.getElementById('paletteSelect').value;

        const res = await fetch('/api/v1/spatial/render-export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ grid, mode, format, palette })
        });

        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `geomatix_${mode}_export.${format}`;
        a.click();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.appEngine = new GeomatiXEngine();
});