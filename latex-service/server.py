#!/usr/bin/env python3
"""
LaTeX Compilation Service
Compiles .tex files to PDF via a simple HTTP API.
"""

import os
import subprocess
import tempfile
import shutil
from flask import Flask, request, jsonify, send_file

app = Flask(__name__)

FILES_DIR = os.environ.get('FILES_DIR', '/files')


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "latex-compiler"})


@app.route('/compile', methods=['POST'])
def compile_tex():
    """
    Compile a .tex file to PDF.
    
    Accepts JSON body with either:
      - file_path: path to .tex file (relative to /files)
      - tex_content: raw LaTeX string
    
    Returns JSON with the output PDF path, or the PDF file directly
    if ?download=true is set.
    """
    data = request.get_json(silent=True) or {}
    
    file_path = data.get('file_path', '')
    tex_content = data.get('tex_content', '')
    
    if file_path:
        # File path mode - compile an existing .tex file
        # Strip /files/ prefix if present (n8n sends full container paths)
        clean_path = file_path.lstrip('/')
        if clean_path.startswith('files/'):
            clean_path = clean_path[len('files/'):]
        full_path = os.path.join(FILES_DIR, clean_path)
        if not os.path.exists(full_path):
            return jsonify({"error": f"File not found: {file_path}"}), 404
        
        with open(full_path, 'r') as f:
            tex_content = f.read()
        
        # Output PDF goes next to the .tex file
        output_dir = os.path.dirname(full_path)
        base_name = os.path.splitext(os.path.basename(full_path))[0]
    elif tex_content:
        # Raw content mode
        output_dir = os.path.join(FILES_DIR, 'output')
        os.makedirs(output_dir, exist_ok=True)
        base_name = data.get('filename', 'resume')
        if base_name.endswith('.tex'):
            base_name = base_name[:-4]
    else:
        return jsonify({"error": "Provide either file_path or tex_content"}), 400
    
    # Create temp directory for compilation
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_file = os.path.join(tmpdir, f"{base_name}.tex")
        
        with open(tex_file, 'w') as f:
            f.write(tex_content)
        
        # Run pdflatex twice (for references)
        for i in range(2):
            result = subprocess.run(
                ['pdflatex', '-interaction=nonstopmode', '-halt-on-error', tex_file],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0 and i == 1:
                # Only fail on second pass
                log_file = os.path.join(tmpdir, f"{base_name}.log")
                log_content = ""
                if os.path.exists(log_file):
                    with open(log_file, 'r') as f:
                        lines = f.readlines()
                    # Get last 50 lines of log
                    log_content = ''.join(lines[-50:])
                
                return jsonify({
                    "error": "LaTeX compilation failed",
                    "log": log_content,
                    "stderr": result.stderr[-500:] if result.stderr else ""
                }), 500
        
        # Copy PDF to output directory
        pdf_source = os.path.join(tmpdir, f"{base_name}.pdf")
        if not os.path.exists(pdf_source):
            return jsonify({"error": "PDF was not generated"}), 500
        
        pdf_dest = os.path.join(output_dir, f"{base_name}.pdf")
        shutil.copy2(pdf_source, pdf_dest)
        
        # Return path relative to FILES_DIR
        relative_path = os.path.relpath(pdf_dest, FILES_DIR)
        
        if request.args.get('download') == 'true':
            return send_file(pdf_dest, mimetype='application/pdf',
                           download_name=f"{base_name}.pdf")
        
        return jsonify({
            "success": True,
            "pdf_path": f"/files/{relative_path}",
            "filename": f"{base_name}.pdf",
            "size_bytes": os.path.getsize(pdf_dest)
        })


@app.route('/compile-and-download', methods=['POST'])
def compile_and_download():
    """Compile and return PDF file directly."""
    request.args = {'download': 'true'}
    return compile_tex()


@app.route('/compile-binary', methods=['POST'])
def compile_binary():
    """
    Stateless compilation endpoint for S3-based workflows.
    Accepts raw LaTeX content, compiles to PDF, and returns the PDF binary.
    No files are written to the local filesystem.

    Body JSON:
      - tex_content: raw LaTeX string (required)
      - filename: base filename without extension (optional, default: 'resume')

    Returns: PDF binary with Content-Type: application/pdf
    """
    data = request.get_json(silent=True) or {}
    tex_content = data.get('tex_content', '')
    if not tex_content:
        return jsonify({"error": "tex_content is required"}), 400

    base_name = data.get('filename', 'resume')
    if base_name.endswith('.tex'):
        base_name = base_name[:-4]

    with tempfile.TemporaryDirectory() as tmpdir:
        tex_file = os.path.join(tmpdir, f"{base_name}.tex")
        with open(tex_file, 'w') as f:
            f.write(tex_content)

        for i in range(2):
            result = subprocess.run(
                ['pdflatex', '-interaction=nonstopmode', '-halt-on-error', tex_file],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0 and i == 1:
                log_file = os.path.join(tmpdir, f"{base_name}.log")
                log_content = ""
                if os.path.exists(log_file):
                    with open(log_file, 'r') as f:
                        lines = f.readlines()
                    log_content = ''.join(lines[-50:])
                return jsonify({
                    "error": "LaTeX compilation failed",
                    "log": log_content,
                    "stderr": result.stderr[-500:] if result.stderr else ""
                }), 500

        pdf_path = os.path.join(tmpdir, f"{base_name}.pdf")
        if not os.path.exists(pdf_path):
            return jsonify({"error": "PDF was not generated"}), 500

        return send_file(
            pdf_path,
            mimetype='application/pdf',
            download_name=f"{base_name}.pdf"
        )


if __name__ == '__main__':
    print("🔧 LaTeX Compilation Service starting on port 3001...")
    app.run(host='0.0.0.0', port=3001, debug=False)
