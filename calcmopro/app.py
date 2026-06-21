"""Desktop interface for CALCMO Pro."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from . import __version__
from .core import (
    compute_results,
    default_output_path,
    format_number,
    generate_excel,
    generate_template,
    read_input_file,
    summarize_results,
)


APP_TITLE = "CALCMO Pro"


class CALCMOApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE} - Moyenne d'Orientation BEPC")
        self.geometry("1180x760")
        self.minsize(1040, 680)
        self.configure(bg="#eef3f8")

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.etablissement_var = tk.StringVar()
        self.annee_var = tk.StringVar(value="2025-2026")
        self.status_var = tk.StringVar(value="Pret")
        self.effectif_var = tk.StringVar(value="0")
        self.mo_moyenne_var = tk.StringVar(value="-")
        self.admis_var = tk.StringVar(value="0")
        self.insuffisants_var = tk.StringVar(value="0")

        self.results: list[dict[str, Any]] = []
        self.worker_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker_running = False

        self._configure_style()
        self._build_layout()
        self.after(150, self._poll_worker)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("App.TFrame", background="#eef3f8")
        style.configure("Surface.TFrame", background="#ffffff", relief="flat")
        style.configure("Sidebar.TFrame", background="#0a1628")
        style.configure("Header.TFrame", background="#ffffff")
        style.configure("Footer.TFrame", background="#ffffff")

        style.configure("Title.TLabel", background="#ffffff", foreground="#0f172a", font=("Segoe UI", 19, "bold"))
        style.configure("Subtitle.TLabel", background="#ffffff", foreground="#64748b", font=("Segoe UI", 9))
        style.configure("Section.TLabel", background="#ffffff", foreground="#0f172a", font=("Segoe UI", 11, "bold"))
        style.configure("Field.TLabel", background="#ffffff", foreground="#334155", font=("Segoe UI", 9, "bold"))
        style.configure("Muted.TLabel", background="#ffffff", foreground="#64748b", font=("Segoe UI", 9))
        style.configure("MetricValue.TLabel", background="#ffffff", foreground="#0f172a", font=("Segoe UI", 18, "bold"))
        style.configure("MetricLabel.TLabel", background="#ffffff", foreground="#64748b", font=("Segoe UI", 8, "bold"))
        style.configure("SidebarTitle.TLabel", background="#0a1628", foreground="#fbbf24", font=("Segoe UI", 18, "bold"))
        style.configure("SidebarText.TLabel", background="#0a1628", foreground="#cbd5e1", font=("Segoe UI", 9))
        style.configure("SidebarMuted.TLabel", background="#0a1628", foreground="#94a3b8", font=("Segoe UI", 8))
        style.configure("Status.TLabel", background="#ffffff", foreground="#1e4d9b", font=("Segoe UI", 9, "bold"))

        style.configure("TEntry", fieldbackground="#ffffff", bordercolor="#cbd5e1", lightcolor="#cbd5e1", darkcolor="#cbd5e1", padding=8)
        style.configure("Primary.TButton", background="#1e4d9b", foreground="#ffffff", borderwidth=0, focusthickness=0, padding=(16, 9), font=("Segoe UI", 9, "bold"))
        style.map("Primary.TButton", background=[("active", "#2563eb"), ("disabled", "#94a3b8")])
        style.configure("Secondary.TButton", background="#e2e8f0", foreground="#0f172a", borderwidth=0, padding=(14, 8), font=("Segoe UI", 9, "bold"))
        style.map("Secondary.TButton", background=[("active", "#cbd5e1"), ("disabled", "#e2e8f0")])
        style.configure("Danger.TButton", background="#fee2e2", foreground="#991b1b", borderwidth=0, padding=(14, 8), font=("Segoe UI", 9, "bold"))
        style.map("Danger.TButton", background=[("active", "#fecaca")])

        style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff", foreground="#0f172a", rowheight=30, borderwidth=0, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#0f2044", foreground="#ffffff", borderwidth=0, font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", "#0f172a")])
        style.configure("Horizontal.TProgressbar", troughcolor="#e2e8f0", background="#38bdf8", bordercolor="#e2e8f0", lightcolor="#38bdf8", darkcolor="#38bdf8")

    def _build_layout(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self, style="Sidebar.TFrame", width=245)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        self._build_sidebar(sidebar)

        main = ttk.Frame(self, style="App.TFrame", padding=(18, 18, 18, 14))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        self._build_header(main)
        self._build_controls(main)
        self._build_preview(main)
        self._build_footer(main)

    def _build_sidebar(self, parent: ttk.Frame) -> None:
        brand = ttk.Frame(parent, style="Sidebar.TFrame", padding=(22, 24, 22, 18))
        brand.grid(row=0, column=0, sticky="ew")
        ttk.Label(brand, text="CALCMO", style="SidebarTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(brand, text="MGA + MO BEPC", style="SidebarText.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Label(brand, text=f"Version {__version__}", style="SidebarMuted.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 0))

        steps = ttk.Frame(parent, style="Sidebar.TFrame", padding=(18, 8, 18, 10))
        steps.grid(row=1, column=0, sticky="ew")
        for index, label in enumerate(["Source Excel", "Apercu eleves", "Export resultats"], start=1):
            row = ttk.Frame(steps, style="Sidebar.TFrame")
            row.grid(row=index, column=0, sticky="ew", pady=6)
            number = tk.Label(row, text=str(index), bg="#1e4d9b", fg="#ffffff", width=3, height=1, font=("Segoe UI", 9, "bold"))
            number.grid(row=0, column=0, padx=(0, 10))
            ttk.Label(row, text=label, style="SidebarText.TLabel").grid(row=0, column=1, sticky="w")

        info = ttk.Frame(parent, style="Sidebar.TFrame", padding=(22, 26, 22, 20))
        info.grid(row=2, column=0, sticky="ew")
        ttk.Label(info, text="Formules", style="SidebarText.TLabel", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(info, text="MGA = (T1 + 2T2 + 2T3) / 5", style="SidebarMuted.TLabel", wraplength=195).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(info, text="MO = Somme[(MGA + BEPC) x coeff] / 12", style="SidebarMuted.TLabel", wraplength=195).grid(row=2, column=0, sticky="w", pady=(6, 0))

        parent.rowconfigure(3, weight=1)
        bottom = ttk.Frame(parent, style="Sidebar.TFrame", padding=(22, 18, 22, 22))
        bottom.grid(row=4, column=0, sticky="sew")
        ttk.Label(bottom, text="CIO / DRENAET", style="SidebarMuted.TLabel").grid(row=0, column=0, sticky="w")

    def _build_header(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent, style="Header.TFrame", padding=(18, 14, 18, 14))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="Moyenne d'Orientation BEPC", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Import Excel, controle, calcul MGA/MO et export professionnel", style="Subtitle.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))

        actions = ttk.Frame(header, style="Header.TFrame")
        actions.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Button(actions, text="Modele Excel", style="Secondary.TButton", command=self.create_template).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="Ouvrir dossier", style="Secondary.TButton", command=self.open_output_folder).grid(row=0, column=1)

    def _build_controls(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent, style="Surface.TFrame", padding=(18, 16, 18, 16))
        controls.grid(row=1, column=0, sticky="ew", pady=(14, 14))
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(4, weight=1)

        ttk.Label(controls, text="Fichier Excel", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        input_entry = ttk.Entry(controls, textvariable=self.input_var)
        input_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 12), padx=(0, 8))
        ttk.Button(controls, text="Parcourir", style="Secondary.TButton", command=self.browse_input).grid(row=1, column=2, sticky="ew", pady=(6, 12))

        ttk.Label(controls, text="Fichier sortie", style="Field.TLabel").grid(row=0, column=3, sticky="w", padx=(18, 0))
        output_entry = ttk.Entry(controls, textvariable=self.output_var)
        output_entry.grid(row=1, column=3, columnspan=2, sticky="ew", pady=(6, 12), padx=(18, 8))
        ttk.Button(controls, text="Choisir", style="Secondary.TButton", command=self.browse_output).grid(row=1, column=5, sticky="ew", pady=(6, 12))

        ttk.Label(controls, text="Etablissement", style="Field.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.etablissement_var).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0), padx=(0, 8))

        ttk.Label(controls, text="Annee scolaire", style="Field.TLabel").grid(row=2, column=3, sticky="w", padx=(18, 0))
        ttk.Entry(controls, textvariable=self.annee_var, width=16).grid(row=3, column=3, sticky="w", pady=(6, 0), padx=(18, 8))

        button_bar = ttk.Frame(controls, style="Surface.TFrame")
        button_bar.grid(row=3, column=4, columnspan=2, sticky="e")
        self.preview_button = ttk.Button(button_bar, text="Analyser", style="Secondary.TButton", command=self.preview)
        self.preview_button.grid(row=0, column=0, padx=(0, 8))
        self.generate_button = ttk.Button(button_bar, text="Generer resultats", style="Primary.TButton", command=self.generate)
        self.generate_button.grid(row=0, column=1)

    def _build_preview(self, parent: ttk.Frame) -> None:
        preview = ttk.Frame(parent, style="Surface.TFrame", padding=(18, 16, 18, 16))
        preview.grid(row=2, column=0, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(2, weight=1)

        top = ttk.Frame(preview, style="Surface.TFrame")
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Apercu des resultats", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(top, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=1, sticky="e")

        metrics = ttk.Frame(preview, style="Surface.TFrame")
        metrics.grid(row=1, column=0, sticky="ew", pady=(14, 14))
        for index in range(4):
            metrics.columnconfigure(index, weight=1)

        self._metric(metrics, 0, "Effectif", self.effectif_var)
        self._metric(metrics, 1, "MO moyenne", self.mo_moyenne_var)
        self._metric(metrics, 2, "Felicitations", self.admis_var)
        self._metric(metrics, 3, "Insuffisants", self.insuffisants_var)

        table_frame = ttk.Frame(preview, style="Surface.TFrame")
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ("rank", "nom", "classe", "redaction", "maths", "pc", "anglais", "total", "mo", "mention")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "rank": "N",
            "nom": "Nom et prenoms",
            "classe": "Classe",
            "redaction": "Redaction",
            "maths": "Maths",
            "pc": "P.C.",
            "anglais": "Anglais",
            "total": "Total",
            "mo": "MO",
            "mention": "Mention",
        }
        widths = {
            "rank": 48,
            "nom": 230,
            "classe": 95,
            "redaction": 92,
            "maths": 90,
            "pc": 80,
            "anglais": 90,
            "total": 90,
            "mo": 82,
            "mention": 120,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], minwidth=widths[column], stretch=column == "nom", anchor="w" if column == "nom" else "center")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        log_frame = ttk.Frame(preview, style="Surface.TFrame")
        log_frame.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        log_frame.columnconfigure(0, weight=1)
        ttk.Label(log_frame, text="Journal", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        self.log = tk.Text(log_frame, height=5, wrap="word", bg="#0a1628", fg="#dbeafe", insertbackground="#ffffff", relief="flat", font=("Consolas", 9), padx=10, pady=8)
        self.log.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.log.configure(state="disabled")

    def _metric(self, parent: ttk.Frame, column: int, label: str, variable: tk.StringVar) -> None:
        frame = ttk.Frame(parent, style="Surface.TFrame", padding=(14, 10, 14, 10))
        frame.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
        ttk.Label(frame, textvariable=variable, style="MetricValue.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text=label.upper(), style="MetricLabel.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))

    def _build_footer(self, parent: ttk.Frame) -> None:
        footer = ttk.Frame(parent, style="Footer.TFrame", padding=(18, 10, 18, 10))
        footer.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        footer.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(footer, mode="indeterminate")
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        ttk.Button(footer, text="Effacer", style="Danger.TButton", command=self.clear).grid(row=0, column=1)

    def browse_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Selectionner le fichier Excel",
            filetypes=[("Fichiers Excel", "*.xlsx *.xlsm *.xls"), ("Tous les fichiers", "*.*")],
        )
        if not path:
            return
        self.input_var.set(path)
        self.output_var.set(str(default_output_path(path)))
        if not self.etablissement_var.get().strip():
            self._try_fill_establishment(path)
        self.preview()

    def browse_output(self) -> None:
        initial = self.output_var.get() or (str(default_output_path(self.input_var.get())) if self.input_var.get() else "CALCMO_RESULTATS.xlsx")
        path = filedialog.asksaveasfilename(
            title="Choisir le fichier de sortie",
            defaultextension=".xlsx",
            initialfile=Path(initial).name,
            initialdir=str(Path(initial).parent) if Path(initial).parent.exists() else str(Path.home()),
            filetypes=[("Classeur Excel", "*.xlsx")],
        )
        if path:
            self.output_var.set(path)

    def preview(self) -> None:
        if not self._validate_input_only():
            return
        self._start_worker("preview", self._worker_preview)

    def generate(self) -> None:
        if not self._validate_for_generation():
            return
        self._start_worker("generate", self._worker_generate)

    def create_template(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Creer un modele de saisie",
            defaultextension=".xlsx",
            initialfile="CALCMO_MODELE_SAISIE.xlsx",
            filetypes=[("Classeur Excel", "*.xlsx")],
        )
        if not path:
            return
        try:
            created = generate_template(path)
        except Exception as exc:  # noqa: BLE001 - UI boundary.
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self._append_log(f"Modele cree : {created}")
        messagebox.showinfo(APP_TITLE, "Modele Excel cree avec succes.")

    def open_output_folder(self) -> None:
        target = self.output_var.get() or self.input_var.get() or str(Path.cwd())
        folder = Path(target).parent if Path(target).suffix else Path(target)
        if not folder.exists():
            folder = Path.cwd()
        os.startfile(folder)  # type: ignore[attr-defined]

    def clear(self) -> None:
        self.results = []
        self.input_var.set("")
        self.output_var.set("")
        self.etablissement_var.set("")
        self.annee_var.set("2025-2026")
        self._set_metrics({})
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._set_log("")
        self.status_var.set("Pret")

    def _try_fill_establishment(self, path: str) -> None:
        try:
            report = read_input_file(path)
        except Exception:
            return
        for student in report.students:
            etablissement = student.get("etablissement", "")
            if etablissement:
                self.etablissement_var.set(etablissement)
                return

    def _validate_input_only(self) -> bool:
        input_path = self.input_var.get().strip()
        if not input_path:
            messagebox.showwarning(APP_TITLE, "Selectionnez un fichier Excel.")
            return False
        if not Path(input_path).exists():
            messagebox.showwarning(APP_TITLE, "Le fichier Excel selectionne est introuvable.")
            return False
        return True

    def _validate_for_generation(self) -> bool:
        if not self._validate_input_only():
            return False
        if not self.output_var.get().strip():
            self.output_var.set(str(default_output_path(self.input_var.get())))
        return True

    def _start_worker(self, action: str, worker: Any) -> None:
        if self.worker_running:
            return
        self.worker_running = True
        self.status_var.set("Traitement en cours...")
        self._set_buttons(False)
        self.progress.start(12)
        self._append_log(f"{action.capitalize()} demarre.")
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _worker_preview(self) -> None:
        try:
            report = read_input_file(self.input_var.get())
            results = compute_results(report.students)
            self.worker_queue.put(("preview_done", {"report": report, "results": results}))
        except Exception as exc:  # noqa: BLE001 - passed to UI.
            self.worker_queue.put(("error", exc))

    def _worker_generate(self) -> None:
        try:
            report = read_input_file(self.input_var.get())
            results = compute_results(report.students)
            output_path = generate_excel(
                results,
                self.output_var.get(),
                etablissement=self.etablissement_var.get().strip(),
                annee=self.annee_var.get().strip() or "2025-2026",
            )
            self.worker_queue.put(("generate_done", {"report": report, "results": results, "output_path": output_path}))
        except Exception as exc:  # noqa: BLE001 - passed to UI.
            self.worker_queue.put(("error", exc))

    def _poll_worker(self) -> None:
        try:
            while True:
                event, payload = self.worker_queue.get_nowait()
                if event == "preview_done":
                    self.results = payload["results"]
                    self._render_results(payload["results"])
                    self._render_warnings(payload["report"].warnings)
                    self.status_var.set("Apercu pret")
                    self._append_log(f"{len(payload['results'])} eleve(s) analyses.")
                    self._finish_worker()
                elif event == "generate_done":
                    self.results = payload["results"]
                    self._render_results(payload["results"])
                    self._render_warnings(payload["report"].warnings)
                    self.status_var.set("Export termine")
                    self._append_log(f"Fichier genere : {payload['output_path']}")
                    self._finish_worker()
                    messagebox.showinfo(APP_TITLE, "Fichier de resultats genere avec succes.")
                elif event == "error":
                    self.status_var.set("Erreur")
                    self._append_log(f"Erreur : {payload}")
                    self._finish_worker()
                    messagebox.showerror(APP_TITLE, str(payload))
        except queue.Empty:
            pass
        self.after(150, self._poll_worker)

    def _finish_worker(self) -> None:
        self.worker_running = False
        self.progress.stop()
        self._set_buttons(True)

    def _set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.preview_button.configure(state=state)
        self.generate_button.configure(state=state)

    def _render_results(self, results: list[dict[str, Any]]) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        for index, result in enumerate(results, start=1):
            subject_mga = []
            for line in result["lignes"]:
                subject_mga.append(format_number(line.get("mga"), 3))
            self.tree.insert(
                "",
                "end",
                values=(
                    index,
                    result["nom"],
                    result["classe"],
                    *subject_mga,
                    format_number(result["total"], 3),
                    format_number(result["mo"], 4),
                    result["mention"],
                ),
            )
        self._set_metrics(summarize_results(results))

    def _set_metrics(self, summary: dict[str, Any]) -> None:
        self.effectif_var.set(str(summary.get("effectif", 0)))
        self.mo_moyenne_var.set(format_number(summary.get("mo_moyenne"), 4))
        self.admis_var.set(str(summary.get("admis", 0)))
        self.insuffisants_var.set(str(summary.get("insuffisants", 0)))

    def _render_warnings(self, warnings: list[str]) -> None:
        if warnings:
            self._append_log("Alertes :")
            for warning in warnings[:12]:
                self._append_log(f"- {warning}")
            if len(warnings) > 12:
                self._append_log(f"- {len(warnings) - 12} alerte(s) supplementaire(s)")

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        if text:
            self.log.insert("end", text)
        self.log.configure(state="disabled")


def main() -> None:
    app = CALCMOApp()
    app.mainloop()


if __name__ == "__main__":
    main()
