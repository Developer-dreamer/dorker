import os
import re
import shutil
import subprocess
import tempfile
from typing import Any

from jinja2 import Environment, FileSystemLoader

LATEX_SUBS = (
    (re.compile(r"\\"), r"\\textbackslash{}"),
    (re.compile(r"([{}_#%&$])"), r"\\\1"),
    (re.compile(r"~"), r"\\textasciitilde{}"),
    (re.compile(r"\^"), r"\\textasciicircum{}"),
    (re.compile(r"<"), r"\\textless{}"),
    (re.compile(r">"), r"\\textgreater{}"),
)


def escape_latex(text: str) -> str:
    """Escapes common LaTeX control characters from LLM text."""
    if not isinstance(text, str):
        return text
    new_text = text
    for pattern, replacement in LATEX_SUBS:
        new_text = pattern.sub(replacement, new_text)
    return new_text


def get_jinja_env(template_dir: str) -> Environment:
    """Configures Jinja2 to avoid syntax collision with TeX curly braces."""
    return Environment(
        loader=FileSystemLoader(template_dir),
        block_start_string=r"\BLOCK{",
        block_end_string=r"}",
        variable_start_string=r"\VAR{",
        variable_end_string=r"}",
        comment_start_string=r"\#{",
        comment_end_string=r"}",
        autoescape=False,
    )


def compile_pdf(tex_content: str, output_pdf_path: str) -> None:
    """Compiles TeX source to PDF in an isolated temporary directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        tex_path = os.path.join(temp_dir, "document.tex")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_content)

        xelatex_bin = shutil.which("xelatex")
        if not xelatex_bin and os.path.exists("/Library/TeX/texbin/xelatex"):
            xelatex_bin = "/Library/TeX/texbin/xelatex"

        if not xelatex_bin:
            raise FileNotFoundError(
                "Executable 'xelatex' not found. Please install MacTeX/BasicTeX (e.g. `brew install --cask mactex-no-gui` or `brew install --cask basictex`)."
            )

        cmd = [xelatex_bin, "-interaction=nonstopmode", "-halt-on-error", "document.tex"]

        result = subprocess.run(
            cmd, cwd=temp_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        if result.returncode != 0:
            raise RuntimeError(f"LaTeX Compilation Failed:\n{result.stdout}")

        generated_pdf = os.path.join(temp_dir, "document.pdf")
        if not os.path.exists(generated_pdf):
            raise FileNotFoundError("PDF was not created despite zero exit code.")

        with open(generated_pdf, "rb") as src, open(output_pdf_path, "wb") as dst:
            dst.write(src.read())


def generate_cover_letter(data: dict[str, Any], template_path: str, output_pdf: str) -> None:
    # 1. Clean and escape LLM input
    sanitized_data: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            sanitized_data[key] = escape_latex(value)
        elif isinstance(value, list):
            sanitized_data[key] = [
                escape_latex(item) if isinstance(item, str) else item for item in value
            ]
        else:
            sanitized_data[key] = value

    # 2. Render TeX
    template_dir = os.path.dirname(os.path.abspath(template_path))
    template_file = os.path.basename(template_path)
    env = get_jinja_env(template_dir)
    template = env.get_template(template_file)
    rendered_tex = template.render(**sanitized_data)

    # 3. Compile
    compile_pdf(rendered_tex, output_pdf)
