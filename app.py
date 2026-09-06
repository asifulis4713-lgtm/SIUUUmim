import os
import io
import uuid
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.enums import Resampling
from PIL import Image, ImageDraw
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from pathlib import Path

# --- CORE FLASK INITIALIZATION ---
app = Flask(__name__, template_folder='app/templates', static_folder='app/static')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB Limit
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'geomatix-spatial-secret-2026')

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / 'uploads'
OUTPUT_DIR = BASE_DIR / 'outputs'

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {'tif', 'tiff', 'jpg', 'jpeg'}

PALETTES = {
    'viridis': [
        (0.267, 0.004, 0.329), (0.282, 0.100, 0.422), (0.277, 0.198, 0.501),
        (0.245, 0.288, 0.548), (0.199, 0.372, 0.556), (0.155, 0.453, 0.552),
        (0.122, 0.533, 0.538), (0.119, 0.613, 0.507), (0.168, 0.690, 0.452),
        (0.293, 0.761, 0.370), (0.478, 0.821, 0.264), (0.701, 0.868, 0.180),
        (0.993, 0.906, 0.144)
    ],
    'turbo': [
        (0.190, 0.071, 0.232), (0.236, 0.222, 0.669), (0.188, 0.418, 0.934),
        (0.107, 0.619, 0.962), (0.079, 0.793, 0.820), (0.219, 0.923, 0.598),
        (0.489, 0.983, 0.332), (0.767, 0.942, 0.147), (0.957, 0.803, 0.088),
        (0.995, 0.589, 0.088), (0.924, 0.334, 0.088), (0.751, 0.126, 0.063)
    ],
    'terrain': [
        (0.200, 0.200, 0.600), (0.200, 0.500, 0.800), (0.300, 0.700, 0.400),
        (0.800, 0.800, 0.500), (0.600, 0.400, 0.200), (0.900, 0.900, 0.900)
    ],
    'grayscale': [
        (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
    ]
}

def is_extension_valid(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def convert_jpg_to_geotiff(jpg_path, target_tiff_path):
    """Converts JPG/JPEG image to a standard GeoTIFF format."""
    with Image.open(jpg_path) as img:
        img_rgb = img.convert('RGB')
        arr = np.array(img_rgb) # Height x Width x Bands
        
        height, width, bands = arr.shape
        transform = from_origin(0, 0, 1, 1) # Default unit affine transform
        
        with rasterio.open(
            target_tiff_path,
            'w',
            driver='GTiff',
            height=height,
            width=width,
            count=bands,
            dtype=arr.dtype,
            crs='EPSG:4326',
            transform=transform
        ) as dst:
            for b in range(bands):
                dst.write(arr[:, :, b], b + 1)

def sample_raster_array(raster_src, band_idx, grid_r, grid_c):
    data = raster_src.read(
        band_idx,
        out_shape=(grid_r, grid_c),
        resampling=Resampling.bilinear
    )
    nodata = raster_src.nodatavals[band_idx - 1]
    
    masked = np.ma.masked_invalid(data.astype(np.float64))
    if nodata is not None:
        masked = np.ma.masked_equal(masked, nodata)
        
    return masked

def interpolate_color(val, palette_name):
    if np.isnan(val) or val is None:
        return (15, 23, 42)
        
    colors = PALETTES.get(palette_name, PALETTES['viridis'])
    val = max(0.0, min(1.0, float(val)))
    
    idx = val * (len(colors) - 1)
    low_i = int(np.floor(idx))
    high_i = min(low_i + 1, len(colors) - 1)
    frac = idx - low_i
    
    c1 = colors[low_i]
    c2 = colors[high_i]
    
    r = int((c1[0] + (c2[0] - c1[0]) * frac) * 255)
    g = int((c1[1] + (c2[1] - c1[1]) * frac) * 255)
    b = int((c1[2] + (c2[2] - c1[2]) * frac) * 255)
    
    return (r, g, b)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/v1/spatial/inspect', methods=['POST'])
def inspect_raster():
    if 'raster_file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file submitted.'}), 400
        
    file = request.files['raster_file']
    if file.filename == '' or not is_extension_valid(file.filename):
        return jsonify({'status': 'error', 'message': 'Invalid file format. Upload .tif, .tiff, .jpg, or .jpeg'}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    unique_id = uuid.uuid4().hex
    filename = f"{unique_id}_{secure_filename(file.filename)}"
    raw_saved_path = UPLOAD_DIR / filename
    file.save(raw_saved_path)

    # JPG/JPEG হলে কনভার্ট করে TIFF বানিয়ে নেওয়া হবে
    if ext in ['jpg', 'jpeg']:
        tiff_filename = f"{unique_id}_converted.tif"
        converted_tiff_path = UPLOAD_DIR / tiff_filename
        try:
            convert_jpg_to_geotiff(raw_saved_path, converted_tiff_path)
            working_file_token = tiff_filename
            working_path = converted_tiff_path
        except Exception as e:
            return jsonify({'status': 'error', 'message': f"JPG Conversion Error: {str(e)}"}), 500
    else:
        working_file_token = filename
        working_path = raw_saved_path

    try:
        with rasterio.open(working_path) as src:
            bands_info = []
            for b_idx in range(1, src.count + 1):
                arr = src.read(b_idx, masked=True)
                nodata = src.nodatavals[b_idx - 1]
                
                valid_data = arr.compressed()
                if valid_data.size > 0:
                    b_min, b_max, b_mean = float(np.min(valid_data)), float(np.max(valid_data)), float(np.mean(valid_data))
                else:
                    b_min, b_max, b_mean = None, None, None

                bands_info.append({
                    'band_index': b_idx,
                    'min': round(b_min, 4) if b_min is not None else 'N/A',
                    'max': round(b_max, 4) if b_max is not None else 'N/A',
                    'mean': round(b_mean, 4) if b_mean is not None else 'N/A',
                    'nodata': nodata if nodata is not None else 'None'
                })

            payload = {
                'status': 'success',
                'file_token': working_file_token,
                'original_name': file.filename,
                'file_type': 'JPG Image (Converted to TIFF)' if ext in ['jpg', 'jpeg'] else 'GeoTIFF Data',
                'dimensions': {'width': src.width, 'height': src.height},
                'band_count': src.count,
                'crs': str(src.crs) if src.crs else 'EPSG:4326 (Default)',
                'datatype': str(src.dtypes[0]),
                'bands': bands_info
            }
            return jsonify(payload)
    except Exception as err:
        return jsonify({'status': 'error', 'message': f"Raster Processing Error: {str(err)}"}), 500

@app.route('/api/v1/spatial/extract-grid', methods=['POST'])
def extract_grid():
    data = request.json or {}
    token = data.get('file_token')
    band_num = int(data.get('band', 1))
    rows = max(5, min(60, int(data.get('rows', 20))))
    cols = max(5, min(60, int(data.get('cols', 20))))

    file_path = UPLOAD_DIR / token
    if not file_path.exists():
        return jsonify({'status': 'error', 'message': 'Dataset context expired or missing.'}), 404

    try:
        with rasterio.open(file_path) as src:
            masked_arr = sample_raster_array(src, band_num, rows, cols)
            grid_matrix = []
            for r in range(rows):
                row_vals = []
                for c in range(cols):
                    v = masked_arr[r, c]
                    row_vals.append(None if np.ma.is_masked(v) or np.isnan(v) else round(float(v), 3))
                grid_matrix.append(row_vals)

            return jsonify({'status': 'success', 'grid': grid_matrix, 'rows': rows, 'cols': cols})
    except Exception as err:
        return jsonify({'status': 'error', 'message': str(err)}), 500

@app.route('/api/v1/spatial/process-normalization', methods=['POST'])
def process_normalization():
    data = request.json or {}
    token = data.get('file_token')

    src_path = UPLOAD_DIR / token
    if not src_path.exists():
        return jsonify({'status': 'error', 'message': 'Source file not found.'}), 404

    output_filename = f"norm_{token.rsplit('.', 1)[0]}.tif"
    dst_path = OUTPUT_DIR / output_filename

    try:
        with rasterio.open(src_path) as src:
            profile = src.profile.copy()
            profile.update(driver='GTiff', dtype=rasterio.float32, count=src.count)

            with rasterio.open(dst_path, 'w', **profile) as dst:
                for b_idx in range(1, src.count + 1):
                    arr = src.read(b_idx).astype(np.float32)
                    nodata = src.nodatavals[b_idx - 1]

                    mask = np.isnan(arr)
                    if nodata is not None:
                        mask = mask | (arr == nodata)

                    valid_pts = arr[~mask]
                    if valid_pts.size > 0:
                        v_min, v_max = np.min(valid_pts), np.max(valid_pts)
                        if v_max != v_min:
                            arr_norm = (arr - v_min) / (v_max - v_min)
                        else:
                            arr_norm = np.zeros_like(arr)
                    else:
                        arr_norm = arr

                    if nodata is not None:
                        arr_norm[mask] = nodata

                    dst.write(arr_norm.astype(rasterio.float32), b_idx)

        return jsonify({'status': 'success', 'normalized_token': output_filename})
    except Exception as err:
        return jsonify({'status': 'error', 'message': f"Normalization processing failed: {str(err)}"}), 500

@app.route('/api/v1/spatial/render-export', methods=['POST'])
def render_export():
    data = request.json or {}
    grid = data.get('grid', [])
    mode = data.get('mode', 'raw')
    palette = data.get('palette', 'viridis')
    export_format = data.get('format', 'csv')

    if not grid:
        return jsonify({'status': 'error', 'message': 'Empty data structure.'}), 400

    r_count = len(grid)
    c_count = len(grid[0])

    if export_format == 'csv':
        output = io.StringIO()
        output.write("Row/Col," + ",".join([f"Col_{c+1}" for c in range(c_count)]) + "\n")
        for r_idx, row in enumerate(grid):
            formatted_row = [str(v) if v is not None else "NoData" for v in row]
            output.write(f"Row_{r_idx+1}," + ",".join(formatted_row) + "\n")

        mem_buf = io.BytesIO()
        mem_buf.write(output.getvalue().encode('utf-8'))
        mem_buf.seek(0)
        return send_file(mem_buf, mimetype="text/csv", as_attachment=True, download_name=f"geomatix_{mode}_matrix.csv")

    elif export_format == 'png':
        cell_size = 40
        img_w = c_count * cell_size
        img_h = r_count * cell_size
        image = Image.new('RGB', (img_w, img_h), (15, 23, 42))
        draw = ImageDraw.Draw(image)

        for r in range(r_count):
            for c in range(c_count):
                val = grid[r][c]
                box = [c * cell_size, r * cell_size, (c + 1) * cell_size, (r + 1) * cell_size]
                
                if val is None:
                    fill = (15, 23, 42)
                elif mode == 'normalized':
                    fill = interpolate_color(val, palette)
                else:
                    fill = (16, 185, 129)

                draw.rectangle(box, fill=fill, outline=(30, 41, 59))

        img_buf = io.BytesIO()
        image.save(img_buf, format='PNG')
        img_buf.seek(0)
        return send_file(img_buf, mimetype="image/png", as_attachment=True, download_name=f"geomatix_{mode}_grid.png")

    return jsonify({'status': 'error', 'message': 'Invalid format selection'}), 400

@app.route('/download/<filename>')
def download_output(filename):
    safe_name = secure_filename(filename)
    target = OUTPUT_DIR / safe_name
    if not target.exists():
        return "Requested raster file expired or not found.", 404
    return send_file(target, as_attachment=True, download_name=f"normalized_{safe_name}")

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)