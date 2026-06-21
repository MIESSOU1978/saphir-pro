"""SAPHIR Pro desktop interface reproduced from the supplied HTML mockup."""

from __future__ import annotations

import json
import os
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from . import __version__
from .core import (
    MATIERES,
    calc_mga,
    calc_mo,
    compute_results,
    default_output_path,
    format_number,
    generate_excel,
    generate_template,
    read_input_file,
    summarize_results,
)


APP_TITLE = "SchoolMetrics - Orientation BEPC"

COLORS = {
    "navy": "#070E18",
    "navy2": "#081524",
    "blue": "#0E2F56",
    "blue2": "#1B50CC",
    "cyan": "#60A5FA",
    "gold": "#fbbf24",
    "orange": "#F59E0B",
    "green": "#0FAC71",
    "green_soft": "#0B2D22",
    "red": "#A7001E",
    "red_soft": "#3B1018",
    "white": "#EDF4FF",
    "muted": "#8BB9D8",
    "soft_text": "#C8DCEE",
    "border": "#152438",
    "line": "#214766",
    "field": "#0D2031",
    "panel": "#102235",
    "header": "#040A12",
    "header_panel": "#081524",
    "hover": "#173552",
}


def resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base.joinpath(*parts)


class SaphirProApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1220x820")
        self.minsize(1080, 720)
        self.configure(bg=COLORS["navy"])
        try:
            self.iconbitmap(resource_path("assets", "SchoolMetrics.ico"))
        except Exception:
            pass

        self.pages: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.brand_images: list[tk.PhotoImage] = []
        self.history_path = Path.home() / "Documents" / "CALCMO" / "saphir_history.json"
        self.history: list[dict[str, str]] = self._load_history()

        self._configure_styles()
        self._build_shell()
        self._build_mga_page()
        self._build_mo_page()
        self._build_excel_page()
        self._build_history_page()
        self.show_page("mga")

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Dark.Treeview",
            background=COLORS["field"],
            fieldbackground=COLORS["field"],
            foreground=COLORS["white"],
            rowheight=30,
            borderwidth=0,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Dark.Treeview.Heading",
            background=COLORS["header_panel"],
            foreground=COLORS["white"],
            borderwidth=0,
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Dark.Treeview", background=[("selected", COLORS["hover"])])
        style.configure(
            "Dark.Vertical.TScrollbar",
            troughcolor=COLORS["header_panel"],
            background=COLORS["blue"],
            bordercolor=COLORS["navy"],
            arrowcolor=COLORS["white"],
        )
        style.configure(
            "TCombobox",
            fieldbackground=COLORS["field"],
            background=COLORS["field"],
            foreground=COLORS["white"],
            arrowcolor=COLORS["cyan"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["field"],
            darkcolor=COLORS["border"],
            padding=(10, 5, 8, 5),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", COLORS["field"]), ("focus", COLORS["panel"])],
            background=[("readonly", COLORS["field"]), ("focus", COLORS["panel"])],
            foreground=[("readonly", COLORS["white"])],
        )

    def _build_shell(self) -> None:
        shell = tk.Frame(self, bg=COLORS["navy"])
        shell.pack(fill="both", expand=True)

        sidebar = tk.Frame(shell, bg=COLORS["header"], width=292)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg=COLORS["header"], padx=26, pady=24)
        brand.pack(fill="x")
        logo = self._load_asset_image("schoolmetrics_logo_header.png", subsample=1)
        if logo:
            tk.Label(brand, image=logo, bg=COLORS["header"], bd=0).pack(anchor="w")
        else:
            tk.Label(brand, text="SchoolMetrics", bg=COLORS["header"], fg=COLORS["white"], font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(brand, text="DRENA Abengourou", bg=COLORS["header"], fg=COLORS["muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(12, 0))
        tk.Label(brand, text=f"Orientation BEPC  |  v{__version__}", bg=COLORS["header"], fg=COLORS["soft_text"], font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 0))

        year_chip = tk.Frame(sidebar, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
        year_chip.pack(fill="x", padx=26, pady=(0, 20))
        tk.Label(year_chip, text="ANNEE : 2025-2026", bg=COLORS["panel"], fg=COLORS["gold"], font=("Segoe UI", 9, "bold"), pady=8).pack()

        tk.Frame(sidebar, bg=COLORS["border"], height=1).pack(fill="x", padx=26, pady=(0, 20))
        tk.Label(sidebar, text="ESPACE DE SUIVI", bg=COLORS["header"], fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=26, pady=(0, 8))
        self._nav_button(sidebar, "mga", "Calcul MGA").pack(fill="x", padx=26, pady=4)
        self._nav_button(sidebar, "mo", "Calcul MO (BEPC)").pack(fill="x", padx=26, pady=4)
        self._nav_button(sidebar, "excel", "Import Excel").pack(fill="x", padx=26, pady=4)
        self._nav_button(sidebar, "hist", "Historique").pack(fill="x", padx=26, pady=4)

        tk.Frame(sidebar, bg=COLORS["border"], height=1).pack(fill="x", padx=26, pady=(24, 14))
        tk.Label(sidebar, text="ADMINISTRATION", bg=COLORS["header"], fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=26)
        tk.Label(
            sidebar,
            text="YAO MIESSOU Alexandre\nInspecteur d'Orientation\nCEL : 08 10 98 40 / 52 03 06 22",
            bg=COLORS["header"],
            fg=COLORS["soft_text"],
            justify="left",
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=26, pady=(10, 0))

        content = tk.Frame(shell, bg=COLORS["navy"])
        content.pack(side="left", fill="both", expand=True)

        top = tk.Frame(content, bg=COLORS["header"], height=96, highlightbackground=COLORS["line"], highlightthickness=1)
        top.pack(fill="x")
        top.pack_propagate(False)
        top.columnconfigure(0, weight=1)
        title_box = tk.Frame(top, bg=COLORS["header"])
        title_box.grid(row=0, column=0, sticky="w", padx=30, pady=18)
        tk.Label(title_box, text="MINISTERE DE L'EDUCATION NATIONALE, DE", bg=COLORS["header"], fg=COLORS["white"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Label(title_box, text="L'ALPHABETISATION ET DE L'ENSEIGNEMENT TECHNIQUE", bg=COLORS["header"], fg=COLORS["white"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Label(title_box, text="DIRECTION REGIONALE D'ABENGOUROU", bg=COLORS["header"], fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(4, 0))

        status = tk.Frame(top, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=1)
        status.grid(row=0, column=1, sticky="e", padx=30)
        tk.Label(status, text="MO BEPC", bg=COLORS["panel"], fg=COLORS["green"], font=("Segoe UI", 9, "bold"), padx=16, pady=8).pack()

        hero = tk.Frame(content, bg=COLORS["navy"])
        hero.pack(fill="x", padx=30, pady=(22, 0))
        tk.Label(hero, text="Tableau de bord", bg=COLORS["navy"], fg=COLORS["gold"], font=("Segoe UI", 21, "bold")).pack(anchor="w")
        tk.Label(hero, text="Calcul MGA, moyenne d'orientation et exports Excel", bg=COLORS["navy"], fg=COLORS["muted"], font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(3, 0))

        self.main = tk.Frame(content, bg=COLORS["navy"])
        self.main.pack(fill="both", expand=True, padx=30, pady=20)

        self.toast_var = tk.StringVar(value="")
        self.toast = tk.Label(self, textvariable=self.toast_var, bg=COLORS["blue2"], fg=COLORS["white"], font=("Segoe UI", 9, "bold"), padx=18, pady=10)

    def _load_asset_image(self, name: str, subsample: int = 1) -> tk.PhotoImage | None:
        path = resource_path("assets", name)
        if not path.exists():
            return None
        try:
            image = tk.PhotoImage(file=path)
            if subsample > 1:
                image = image.subsample(subsample, subsample)
            self.brand_images.append(image)
            return image
        except Exception:
            return None

    def _nav_button(self, parent: tk.Frame, page: str, text: str) -> tk.Button:
        button = tk.Button(
            parent,
            text="  " + text,
            bg=COLORS["header"],
            fg=COLORS["soft_text"],
            activebackground=COLORS["hover"],
            activeforeground=COLORS["white"],
            bd=0,
            padx=12,
            pady=12,
            anchor="w",
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            command=lambda: self.show_page(page),
        )
        self.nav_buttons[page] = button
        return button

    def _nav_sep(self, parent: tk.Frame) -> tk.Frame:
        return tk.Frame(parent, bg=COLORS["border"], width=1, height=30)

    def _page(self, key: str) -> tk.Frame:
        page = tk.Frame(self.main, bg=COLORS["navy"])
        self.pages[key] = page
        return page

    def _card(self, parent: tk.Frame, pady: tuple[int, int] = (0, 16)) -> tk.Frame:
        card = tk.Frame(parent, bg=COLORS["border"])
        card.pack(fill="x", pady=pady)
        inner = tk.Frame(card, bg=COLORS["header_panel"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        body = tk.Frame(inner, bg=COLORS["header_panel"])
        body.pack(fill="both", expand=True, padx=20, pady=18)
        tk.Frame(inner, bg=COLORS["blue2"], height=2).pack(fill="x", side="top")
        return body

    def _section_title(self, parent: tk.Frame, text: str) -> None:
        head = tk.Frame(parent, bg=COLORS["header_panel"])
        head.pack(fill="x")
        tk.Frame(head, bg=COLORS["blue2"], width=4, height=18).pack(side="left", padx=(0, 10))
        tk.Label(head, text=text.upper(), bg=COLORS["header_panel"], fg=COLORS["white"], font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Frame(parent, bg=COLORS["border"], height=1).pack(fill="x", pady=(6, 14))

    def _field(self, parent: tk.Frame, label: str, variable: tk.StringVar | None = None, width: int = 26) -> tk.Entry:
        tk.Label(parent, text=label.upper(), bg=COLORS["header_panel"], fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 5))
        entry = tk.Entry(
            parent,
            textvariable=variable,
            width=width,
            bg=COLORS["field"],
            fg=COLORS["white"],
            insertbackground=COLORS["white"],
            relief="flat",
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["blue2"],
            highlightthickness=1,
            font=("Segoe UI", 10),
        )
        entry.pack(fill="x", ipady=7)
        return entry

    def _note_entry(self, parent: tk.Frame, variable: tk.StringVar, command: Any, width: int = 9) -> tk.Entry:
        entry = tk.Entry(
            parent,
            textvariable=variable,
            width=width,
            justify="center",
            bg=COLORS["field"],
            fg=COLORS["white"],
            insertbackground=COLORS["white"],
            relief="flat",
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["blue2"],
            highlightthickness=1,
            font=("Segoe UI", 10, "bold"),
        )
        entry.pack(ipady=6)
        variable.trace_add("write", lambda *_: command())
        return entry

    def _button(self, parent: tk.Frame, text: str, command: Any, kind: str = "ghost") -> tk.Button:
        palette = {
            "primary": (COLORS["blue2"], COLORS["white"]),
            "accent": (COLORS["blue"], COLORS["white"]),
            "success": (COLORS["green"], COLORS["white"]),
            "gold": (COLORS["gold"], "#111827"),
            "ghost": (COLORS["field"], COLORS["soft_text"]),
            "danger": (COLORS["red"], COLORS["white"]),
        }
        bg, fg = palette[kind]
        return tk.Button(
            parent,
            text=text.upper(),
            command=command,
            bg=bg,
            fg=fg,
            activebackground=COLORS["hover"],
            activeforeground=COLORS["white"],
            bd=0,
            padx=18,
            pady=9,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
        )

    def show_page(self, key: str) -> None:
        for page in self.pages.values():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        for nav_key, button in self.nav_buttons.items():
            active = nav_key == key
            button.configure(bg=COLORS["blue"] if active else COLORS["header"], fg=COLORS["white"] if active else COLORS["soft_text"])
        if key == "hist":
            self._render_history()

    # ------------------------------------------------------------------ MGA
    def _build_mga_page(self) -> None:
        page = self._page("mga")
        self.mga_info_vars = {
            "nom": tk.StringVar(),
            "etablissement": tk.StringVar(),
            "classe": tk.StringVar(),
            "annee": tk.StringVar(value="2025-2026"),
            "table": tk.StringVar(),
        }
        self.mga_note_vars: dict[str, dict[str, tk.StringVar]] = {}
        self.mga_result_vars: dict[str, tk.StringVar] = {}

        info = self._card(page)
        self._section_title(info, "Informations de l'eleve")
        grid = tk.Frame(info, bg=COLORS["navy2"])
        grid.pack(fill="x")
        for index in range(3):
            grid.columnconfigure(index, weight=1)

        nom_frame = tk.Frame(grid, bg=COLORS["navy2"])
        nom_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=(0, 12), pady=(0, 12))
        self._field(nom_frame, "Nom et prenoms", self.mga_info_vars["nom"])

        etab_frame = tk.Frame(grid, bg=COLORS["navy2"])
        etab_frame.grid(row=0, column=2, sticky="ew", pady=(0, 12))
        self._field(etab_frame, "Etablissement", self.mga_info_vars["etablissement"])

        classe_frame = tk.Frame(grid, bg=COLORS["navy2"])
        classe_frame.grid(row=1, column=0, sticky="ew", padx=(0, 12))
        tk.Label(classe_frame, text="CLASSE", bg=COLORS["navy2"], fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 5))
        classe = ttk.Combobox(classe_frame, textvariable=self.mga_info_vars["classe"], values=["3eme A", "3eme B", "3eme C", "3eme D"], state="normal")
        classe.pack(fill="x", ipady=5)

        annee_frame = tk.Frame(grid, bg=COLORS["navy2"])
        annee_frame.grid(row=1, column=1, sticky="ew", padx=(0, 12))
        self._field(annee_frame, "Annee scolaire", self.mga_info_vars["annee"])

        table_frame = tk.Frame(grid, bg=COLORS["navy2"])
        table_frame.grid(row=1, column=2, sticky="ew")
        self._field(table_frame, "N de table", self.mga_info_vars["table"])

        table_card = self._card(page)
        self._section_title(table_card, "Moyennes trimestrielles par matiere - calcul automatique de la MGA")
        self._build_mga_table(table_card)

        actions = tk.Frame(page, bg=COLORS["navy"])
        actions.pack(fill="x", pady=(0, 16))
        self._button(actions, "Reinitialiser", self.reset_mga, "ghost").pack(side="right", padx=(8, 0))
        self._button(actions, "Transferer vers MO", self.transfer_mga_to_mo, "success").pack(side="right")

        formula = tk.Label(
            page,
            text="Formule MGA ponderee : MGA = (T1 + 2*T2 + 2*T3) / 5. Les notes disponibles sont utilisees si une periode manque.",
            bg=COLORS["navy2"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
            padx=16,
            pady=12,
            anchor="w",
        )
        formula.pack(fill="x")

    def _build_mga_table(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg=COLORS["blue"])
        header.pack(fill="x", pady=(0, 6))
        columns = [("Matiere", 2), ("1er Trimestre", 1), ("2e Trimestre", 1), ("3e Trimestre", 1), ("MGA", 1)]
        for index, (label, weight) in enumerate(columns):
            header.columnconfigure(index, weight=weight)
            tk.Label(header, text=label.upper(), bg=COLORS["blue"], fg=COLORS["cyan"], font=("Segoe UI", 8, "bold"), pady=8).grid(row=0, column=index, sticky="ew")

        body = tk.Frame(parent, bg=COLORS["navy2"])
        body.pack(fill="x")
        for row, matiere in enumerate(MATIERES):
            mid = matiere["id"]
            self.mga_note_vars[mid] = {key: tk.StringVar() for key in ("t1", "t2", "t3")}
            self.mga_result_vars[mid] = tk.StringVar(value="0,000")

            line = tk.Frame(body, bg=COLORS["field"], highlightbackground="#19375f", highlightthickness=1)
            line.pack(fill="x", pady=3)
            for index, (_, weight) in enumerate(columns):
                line.columnconfigure(index, weight=weight)
            tk.Label(line, text=matiere["label"], bg=COLORS["field"], fg=COLORS["gold"], font=("Segoe UI", 10, "bold"), anchor="w", padx=12).grid(row=0, column=0, sticky="ew", ipady=9)
            for col, key in enumerate(("t1", "t2", "t3"), start=1):
                cell = tk.Frame(line, bg=COLORS["field"])
                cell.grid(row=0, column=col)
                self._note_entry(cell, self.mga_note_vars[mid][key], lambda mid=mid: self.update_mga(mid))
            tk.Label(line, textvariable=self.mga_result_vars[mid], bg=COLORS["field"], fg=COLORS["gold"], font=("Segoe UI", 12, "bold")).grid(row=0, column=4, sticky="ew")

    def update_mga(self, mid: str) -> None:
        notes = self.mga_note_vars[mid]
        value = calc_mga(self._parse_float(notes["t1"].get()), self._parse_float(notes["t2"].get()), self._parse_float(notes["t3"].get()))
        self.mga_result_vars[mid].set(format_number(value, 3) if value is not None else "0,000")

    def reset_mga(self) -> None:
        for matiere in MATIERES:
            mid = matiere["id"]
            for key in ("t1", "t2", "t3"):
                self.mga_note_vars[mid][key].set("")
            self.mga_result_vars[mid].set("0,000")
        self._toast("MGA reinitialisee.")

    def transfer_mga_to_mo(self) -> None:
        self.mo_info_vars["nom"].set(self.mga_info_vars["nom"].get())
        self.mo_info_vars["classe"].set(self.mga_info_vars["classe"].get())
        for matiere in MATIERES:
            mid = matiere["id"]
            value = self._parse_float(self.mga_result_vars[mid].get())
            self.mo_vars[mid]["mga"].set(f"{value:.3f}" if value is not None and value > 0 else "")
        self.mga_injected_var.set("MGA injectees depuis le module MGA")
        self.update_mo()
        self.show_page("mo")
        self._toast("MGA transferees vers le module MO.")

    # ------------------------------------------------------------------- MO
    def _build_mo_page(self) -> None:
        page = self._page("mo")
        self.mo_info_vars = {"nom": tk.StringVar(), "classe": tk.StringVar()}
        self.mo_vars: dict[str, dict[str, tk.StringVar]] = {}
        self.mo_sum_vars: dict[str, tk.StringVar] = {}
        self.mo_prod_vars: dict[str, tk.StringVar] = {}
        self.total_var = tk.StringVar(value="0,000")
        self.mo_var = tk.StringVar(value="0,0000")
        self.coeff_var = tk.StringVar(value="6")
        self.mention_var = tk.StringVar(value="-")
        self.mga_injected_var = tk.StringVar(value="")

        info = self._card(page)
        self._section_title(info, "Rappel eleve")
        tk.Label(info, textvariable=self.mga_injected_var, bg=COLORS["navy2"], fg=COLORS["green"], font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 10))
        grid = tk.Frame(info, bg=COLORS["navy2"])
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=2)
        grid.columnconfigure(1, weight=1)
        nom_frame = tk.Frame(grid, bg=COLORS["navy2"])
        nom_frame.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self._field(nom_frame, "Nom et prenoms", self.mo_info_vars["nom"])
        classe_frame = tk.Frame(grid, bg=COLORS["navy2"])
        classe_frame.grid(row=0, column=1, sticky="ew")
        self._field(classe_frame, "Classe", self.mo_info_vars["classe"])

        table = self._card(page)
        self._section_title(table, "Saisie MGA et notes au BEPC - calcul de la Moyenne d'Orientation")
        self._build_mo_table(table)

        result = self._card(page)
        result_grid = tk.Frame(result, bg=COLORS["navy2"])
        result_grid.pack(fill="x")
        for index in range(5):
            result_grid.columnconfigure(index, weight=1 if index in (0, 2, 4) else 0)
        self._result_block(result_grid, 0, "TOTAL (MGA+BEPC)*COEFF.", self.total_var, COLORS["gold"])
        self._result_divider(result_grid, 1)
        self._result_block(result_grid, 2, "MOYENNE D'ORIENTATION", self.mo_var, COLORS["green"], self.mention_var)
        self._result_divider(result_grid, 3)
        self._result_block(result_grid, 4, "SOMME COEFFICIENTS", self.coeff_var, COLORS["cyan"])

        tk.Label(
            page,
            text="Formule MO : MO = somme[(MGA + Note BEPC) * coeff.] / (2 * somme coeff.) | Anglais : BEPC = (Ecrit + Oral) / 2",
            bg=COLORS["navy2"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
            padx=16,
            pady=12,
            anchor="w",
        ).pack(fill="x", pady=(0, 16))

        actions = tk.Frame(page, bg=COLORS["navy"])
        actions.pack(fill="x")
        self._button(actions, "Reinitialiser", self.reset_mo, "ghost").pack(side="right", padx=(8, 0))
        self._button(actions, "Enregistrer", self.save_history, "gold").pack(side="right", padx=(8, 0))
        self._button(actions, "Exporter Excel", self.export_current_student, "accent").pack(side="right")

    def _build_mo_table(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg=COLORS["blue"])
        header.pack(fill="x", pady=(0, 6))
        columns = [("Matiere", 2), ("MGA", 1), ("Note BEPC", 2), ("MGA+BEPC", 1), ("Coeff.", 1), ("Produit", 1)]
        for index, (label, weight) in enumerate(columns):
            header.columnconfigure(index, weight=weight)
            tk.Label(header, text=label.upper(), bg=COLORS["blue"], fg=COLORS["cyan"], font=("Segoe UI", 8, "bold"), pady=8).grid(row=0, column=index, sticky="ew")

        body = tk.Frame(parent, bg=COLORS["navy2"])
        body.pack(fill="x")
        for matiere in MATIERES:
            mid = matiere["id"]
            self.mo_vars[mid] = {"mga": tk.StringVar()}
            if matiere["anglais"]:
                self.mo_vars[mid]["bepc_ecrit"] = tk.StringVar()
                self.mo_vars[mid]["bepc_oral"] = tk.StringVar()
            else:
                self.mo_vars[mid]["bepc"] = tk.StringVar()
            self.mo_sum_vars[mid] = tk.StringVar(value="-")
            self.mo_prod_vars[mid] = tk.StringVar(value="-")

            line = tk.Frame(body, bg=COLORS["field"], highlightbackground="#19375f", highlightthickness=1)
            line.pack(fill="x", pady=3)
            for index, (_, weight) in enumerate(columns):
                line.columnconfigure(index, weight=weight)
            tk.Label(line, text=matiere["label"], bg=COLORS["field"], fg=COLORS["gold"], font=("Segoe UI", 10, "bold"), anchor="w", padx=12).grid(row=0, column=0, sticky="ew", ipady=8)

            mga_cell = tk.Frame(line, bg=COLORS["field"])
            mga_cell.grid(row=0, column=1)
            self._note_entry(mga_cell, self.mo_vars[mid]["mga"], self.update_mo)

            bepc_cell = tk.Frame(line, bg=COLORS["field"])
            bepc_cell.grid(row=0, column=2)
            if matiere["anglais"]:
                row = tk.Frame(bepc_cell, bg=COLORS["field"])
                row.pack()
                left = tk.Frame(row, bg=COLORS["field"])
                left.pack(side="left", padx=3)
                self._note_entry(left, self.mo_vars[mid]["bepc_ecrit"], self.update_mo, width=8)
                tk.Label(left, text="Ecrit", bg=COLORS["field"], fg=COLORS["muted"], font=("Segoe UI", 7)).pack()
                right = tk.Frame(row, bg=COLORS["field"])
                right.pack(side="left", padx=3)
                self._note_entry(right, self.mo_vars[mid]["bepc_oral"], self.update_mo, width=8)
                tk.Label(right, text="Oral", bg=COLORS["field"], fg=COLORS["muted"], font=("Segoe UI", 7)).pack()
            else:
                self._note_entry(bepc_cell, self.mo_vars[mid]["bepc"], self.update_mo)

            tk.Label(line, textvariable=self.mo_sum_vars[mid], bg=COLORS["field"], fg=COLORS["orange"], font=("Segoe UI", 11, "bold")).grid(row=0, column=3, sticky="ew")
            tk.Label(line, text=str(matiere["coef"]), bg=COLORS["blue2"], fg=COLORS["white"], font=("Segoe UI", 10, "bold"), padx=12, pady=6).grid(row=0, column=4)
            tk.Label(line, textvariable=self.mo_prod_vars[mid], bg=COLORS["field"], fg=COLORS["green"], font=("Segoe UI", 11, "bold")).grid(row=0, column=5, sticky="ew")

    def _result_block(self, parent: tk.Frame, column: int, label: str, variable: tk.StringVar, color: str, badge: tk.StringVar | None = None) -> None:
        frame = tk.Frame(parent, bg=COLORS["navy2"])
        frame.grid(row=0, column=column, sticky="ew")
        tk.Label(frame, text=label, bg=COLORS["navy2"], fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack()
        tk.Label(frame, textvariable=variable, bg=COLORS["navy2"], fg=color, font=("Segoe UI", 25, "bold")).pack(pady=(4, 0))
        if badge is not None:
            tk.Label(frame, textvariable=badge, bg=COLORS["blue"], fg=COLORS["cyan"], font=("Segoe UI", 9, "bold"), padx=10, pady=3).pack(pady=(2, 0))

    def _result_divider(self, parent: tk.Frame, column: int) -> None:
        tk.Frame(parent, bg=COLORS["border"], width=1).grid(row=0, column=column, sticky="ns", padx=14)

    def update_mo(self) -> None:
        result = calc_mo(self._student_from_mo())
        for line in result["lignes"]:
            mid = next(m["id"] for m in MATIERES if m["label"] == line["matiere"])
            self.mo_sum_vars[mid].set(format_number(line["somme"], 3) if line["somme"] is not None else "-")
            self.mo_prod_vars[mid].set(format_number(line["produit"], 3) if line["produit"] is not None else "-")
        self.total_var.set(format_number(result["total"], 3))
        self.mo_var.set(format_number(result["mo"], 4))
        self.coeff_var.set("6")
        self.mention_var.set(result["mention"] if result["total"] > 0 else "-")

    def reset_mo(self) -> None:
        for matiere in MATIERES:
            for variable in self.mo_vars[matiere["id"]].values():
                variable.set("")
            self.mo_sum_vars[matiere["id"]].set("-")
            self.mo_prod_vars[matiere["id"]].set("-")
        self.total_var.set("0,000")
        self.mo_var.set("0,0000")
        self.coeff_var.set("6")
        self.mention_var.set("-")
        self.mga_injected_var.set("")
        self._toast("Module MO reinitialise.")

    def _student_from_mo(self) -> dict[str, Any]:
        matieres: dict[str, dict[str, float | None]] = {}
        for matiere in MATIERES:
            mid = matiere["id"]
            if matiere["anglais"]:
                matieres[mid] = {
                    "mga": self._parse_float(self.mo_vars[mid]["mga"].get()),
                    "bepc_ecrit": self._parse_float(self.mo_vars[mid]["bepc_ecrit"].get()),
                    "bepc_oral": self._parse_float(self.mo_vars[mid]["bepc_oral"].get()),
                }
            else:
                matieres[mid] = {
                    "mga": self._parse_float(self.mo_vars[mid]["mga"].get()),
                    "bepc": self._parse_float(self.mo_vars[mid]["bepc"].get()),
                }
        return {
            "nom": self.mo_info_vars["nom"].get().strip(),
            "classe": self.mo_info_vars["classe"].get().strip(),
            "etablissement": self.mga_info_vars["etablissement"].get().strip(),
            "matieres": matieres,
        }

    def save_history(self) -> None:
        self.update_mo()
        student = self._student_from_mo()
        result = calc_mo(student)
        name = result["nom"].strip()
        if not name:
            self._toast("Entrez le nom de l'eleve.", error=True)
            return
        item = {
            "nom": name,
            "classe": result["classe"] or "-",
            "etablissement": result["etablissement"] or "-",
            "total": format_number(result["total"], 3),
            "mo": format_number(result["mo"], 4),
            "mention": result["mention"],
            "date": datetime.now().strftime("%d/%m/%Y"),
        }
        self.history.insert(0, item)
        self._save_history()
        self._toast(f"Enregistre : {name}")

    def export_current_student(self) -> None:
        self.update_mo()
        result = calc_mo(self._student_from_mo())
        if not result["nom"].strip():
            self._toast("Entrez le nom de l'eleve avant l'export.", error=True)
            return
        path = filedialog.asksaveasfilename(
            title="Exporter la fiche MO",
            defaultextension=".xlsx",
            initialfile=f"{self._safe_name(result['nom'])}_MO.xlsx",
            filetypes=[("Classeur Excel", "*.xlsx")],
        )
        if not path:
            return
        try:
            generate_excel([result], path, self.mga_info_vars["etablissement"].get(), self.mga_info_vars["annee"].get())
        except Exception as exc:  # noqa: BLE001 - UI boundary.
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self._toast("Fiche Excel exportee.")

    # --------------------------------------------------------------- Excel pro
    def _build_excel_page(self) -> None:
        page = self._page("excel")
        self.excel_vars = {
            "input": tk.StringVar(),
            "output": tk.StringVar(),
            "etablissement": tk.StringVar(),
            "annee": tk.StringVar(value="2025-2026"),
        }

        controls = self._card(page)
        self._section_title(controls, "Traitement Excel professionnel")
        grid = tk.Frame(controls, bg=COLORS["navy2"])
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(2, weight=1)

        in_frame = tk.Frame(grid, bg=COLORS["navy2"])
        in_frame.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 12))
        self._field(in_frame, "Fichier Excel source", self.excel_vars["input"])
        self._button(grid, "Parcourir", self.browse_excel_input, "ghost").grid(row=0, column=1, sticky="s", pady=(0, 12))

        out_frame = tk.Frame(grid, bg=COLORS["navy2"])
        out_frame.grid(row=0, column=2, sticky="ew", padx=(16, 8), pady=(0, 12))
        self._field(out_frame, "Fichier de sortie", self.excel_vars["output"])
        self._button(grid, "Choisir", self.browse_excel_output, "ghost").grid(row=0, column=3, sticky="s", pady=(0, 12))

        etab_frame = tk.Frame(grid, bg=COLORS["navy2"])
        etab_frame.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self._field(etab_frame, "Etablissement", self.excel_vars["etablissement"])
        annee_frame = tk.Frame(grid, bg=COLORS["navy2"])
        annee_frame.grid(row=1, column=2, sticky="ew", padx=(16, 8))
        self._field(annee_frame, "Annee scolaire", self.excel_vars["annee"])

        actions = tk.Frame(controls, bg=COLORS["navy2"])
        actions.pack(fill="x", pady=(16, 0))
        self._button(actions, "Modele Excel", self.create_template, "gold").pack(side="left")
        self._button(actions, "Analyser", self.analyze_excel, "ghost").pack(side="right", padx=(8, 0))
        self._button(actions, "Generer resultats", self.generate_excel_results, "primary").pack(side="right")

        preview = self._card(page, pady=(0, 0))
        self._section_title(preview, "Apercu des resultats")
        metrics = tk.Frame(preview, bg=COLORS["navy2"])
        metrics.pack(fill="x", pady=(0, 12))
        self.excel_metric_vars = {
            "effectif": tk.StringVar(value="0"),
            "mo": tk.StringVar(value="-"),
            "admis": tk.StringVar(value="0"),
            "insuff": tk.StringVar(value="0"),
        }
        for index, (label, variable, color) in enumerate(
            [
                ("Effectif", self.excel_metric_vars["effectif"], COLORS["cyan"]),
                ("MO moyenne", self.excel_metric_vars["mo"], COLORS["green"]),
                ("Felicitations", self.excel_metric_vars["admis"], COLORS["gold"]),
                ("Insuffisants", self.excel_metric_vars["insuff"], COLORS["red"]),
            ]
        ):
            metrics.columnconfigure(index, weight=1)
            block = tk.Frame(metrics, bg=COLORS["field"], highlightbackground=COLORS["border"], highlightthickness=1)
            block.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0))
            tk.Label(block, textvariable=variable, bg=COLORS["field"], fg=color, font=("Segoe UI", 18, "bold")).pack(pady=(8, 0))
            tk.Label(block, text=label.upper(), bg=COLORS["field"], fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack(pady=(0, 8))

        tree_frame = tk.Frame(preview, bg=COLORS["navy2"])
        tree_frame.pack(fill="both", expand=True)
        columns = ("n", "nom", "classe", "total", "mo", "mention")
        self.excel_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=9, style="Dark.Treeview")
        for key, label, width in [
            ("n", "N", 50),
            ("nom", "Nom et prenoms", 320),
            ("classe", "Classe", 100),
            ("total", "Total", 100),
            ("mo", "MO", 100),
            ("mention", "Mention", 140),
        ]:
            self.excel_tree.heading(key, text=label)
            self.excel_tree.column(key, width=width, anchor="w" if key == "nom" else "center", stretch=key == "nom")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.excel_tree.yview, style="Dark.Vertical.TScrollbar")
        self.excel_tree.configure(yscrollcommand=scroll.set)
        self.excel_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def browse_excel_input(self) -> None:
        path = filedialog.askopenfilename(title="Selectionner le fichier Excel", filetypes=[("Excel", "*.xlsx *.xlsm *.xls"), ("Tous les fichiers", "*.*")])
        if not path:
            return
        self.excel_vars["input"].set(path)
        self.excel_vars["output"].set(str(default_output_path(path)))

    def browse_excel_output(self) -> None:
        initial = self.excel_vars["output"].get() or "CALCMO_RESULTATS.xlsx"
        path = filedialog.asksaveasfilename(title="Choisir le fichier de sortie", defaultextension=".xlsx", initialfile=Path(initial).name, filetypes=[("Classeur Excel", "*.xlsx")])
        if path:
            self.excel_vars["output"].set(path)

    def create_template(self) -> None:
        path = filedialog.asksaveasfilename(title="Creer un modele de saisie", defaultextension=".xlsx", initialfile="CALCMO_MODELE_SAISIE.xlsx", filetypes=[("Classeur Excel", "*.xlsx")])
        if not path:
            return
        try:
            generate_template(path)
        except Exception as exc:  # noqa: BLE001 - UI boundary.
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self._toast("Modele Excel cree.")

    def analyze_excel(self) -> None:
        results = self._load_excel_results()
        if results is not None:
            self._render_excel_results(results)
            self._toast(f"{len(results)} eleve(s) analyses.")

    def generate_excel_results(self) -> None:
        results = self._load_excel_results()
        if results is None:
            return
        output = self.excel_vars["output"].get().strip()
        if not output:
            output = str(default_output_path(self.excel_vars["input"].get()))
            self.excel_vars["output"].set(output)
        try:
            generate_excel(results, output, self.excel_vars["etablissement"].get(), self.excel_vars["annee"].get())
        except Exception as exc:  # noqa: BLE001 - UI boundary.
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self._render_excel_results(results)
        self._toast("Classeur resultats genere.")

    def _load_excel_results(self) -> list[dict[str, Any]] | None:
        path = self.excel_vars["input"].get().strip()
        if not path:
            self._toast("Selectionnez un fichier Excel.", error=True)
            return None
        try:
            report = read_input_file(path)
            results = compute_results(report.students)
        except Exception as exc:  # noqa: BLE001 - UI boundary.
            messagebox.showerror(APP_TITLE, str(exc))
            return None
        if report.warnings:
            messagebox.showwarning(APP_TITLE, "\n".join(report.warnings[:8]))
        return results

    def _render_excel_results(self, results: list[dict[str, Any]]) -> None:
        for item in self.excel_tree.get_children():
            self.excel_tree.delete(item)
        for index, result in enumerate(results, start=1):
            self.excel_tree.insert("", "end", values=(index, result["nom"], result["classe"], format_number(result["total"], 3), format_number(result["mo"], 4), result["mention"]))
        summary = summarize_results(results)
        self.excel_metric_vars["effectif"].set(str(summary["effectif"]))
        self.excel_metric_vars["mo"].set(format_number(summary["mo_moyenne"], 4))
        self.excel_metric_vars["admis"].set(str(summary["admis"]))
        self.excel_metric_vars["insuff"].set(str(summary["insuffisants"]))

    # --------------------------------------------------------------- History
    def _build_history_page(self) -> None:
        page = self._page("hist")
        card = self._card(page, pady=(0, 0))
        self._section_title(card, "Historique des calculs enregistres")
        tree_frame = tk.Frame(card, bg=COLORS["navy2"])
        tree_frame.pack(fill="both", expand=True)
        columns = ("nom", "classe", "etab", "date", "mo", "mention")
        self.history_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=15, style="Dark.Treeview")
        for key, label, width in [
            ("nom", "Nom", 300),
            ("classe", "Classe", 100),
            ("etab", "Etablissement", 220),
            ("date", "Date", 100),
            ("mo", "MO", 100),
            ("mention", "Mention", 140),
        ]:
            self.history_tree.heading(key, text=label)
            self.history_tree.column(key, width=width, anchor="w" if key in ("nom", "etab") else "center", stretch=key == "nom")
        self.history_tree.pack(fill="both", expand=True)

        actions = tk.Frame(card, bg=COLORS["navy2"])
        actions.pack(fill="x", pady=(14, 0))
        self._button(actions, "Vider l'historique", self.clear_history, "danger").pack(side="right")

    def _render_history(self) -> None:
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        for item in self.history:
            self.history_tree.insert("", "end", values=(item["nom"], item["classe"], item["etablissement"], item["date"], item["mo"], item["mention"]))

    def clear_history(self) -> None:
        if not self.history:
            self._toast("Historique deja vide.")
            return
        if not messagebox.askyesno(APP_TITLE, "Vider tout l'historique ?"):
            return
        self.history = []
        self._save_history()
        self._render_history()
        self._toast("Historique vide.")

    def _load_history(self) -> list[dict[str, str]]:
        try:
            if self.history_path.exists():
                data = json.loads(self.history_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
        except Exception:
            return []
        return []

    def _save_history(self) -> None:
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            self.history_path.write_text(json.dumps(self.history, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - UI boundary.
            messagebox.showwarning(APP_TITLE, f"Historique non sauvegarde : {exc}")

    # ---------------------------------------------------------------- Utils
    def _parse_float(self, value: str | None) -> float | None:
        if value is None:
            return None
        text = str(value).strip().replace(",", ".")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _toast(self, message: str, error: bool = False) -> None:
        self.toast_var.set(message)
        self.toast.configure(bg=COLORS["red"] if error else COLORS["blue2"])
        self.toast.place(relx=1.0, rely=1.0, x=-24, y=-24, anchor="se")
        self.after(3200, self.toast.place_forget)

    def _safe_name(self, value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.strip())
        return cleaned.strip("_") or "ELEVE"

    def open_folder(self, path: str) -> None:
        target = Path(path)
        folder = target.parent if target.suffix else target
        if folder.exists():
            os.startfile(folder)  # type: ignore[attr-defined]


def main() -> None:
    app = SaphirProApp()
    app.mainloop()


if __name__ == "__main__":
    main()
