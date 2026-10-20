#!/usr/bin/env python3
"""
budget-gui.py - Desktop Budget & Rechnungs-Manager für Sebastian auf Big Boss.
Kommuniziert mit dem Büro-Service auf Fox (http://100.93.184.87:5005).

Cluster-Regeln:
- Status-Unterscheidung rein typografisch:
  * 'offen': normale Schriftgröße, regulärer Text ('Rechnung offen')
  * 'bezahlt': DEUTLICH GRÖSSER und FETT formatiert ('RECHNUNG BEZAHLT')
- Kein Farbcode-Zwang.
"""

import sys
import os
import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import requests

API_HOST = os.getenv("BUDGET_API_HOST", "100.93.184.87")
API_PORT = int(os.getenv("BUDGET_API_PORT", "5005"))
API_BASE = f"http://{API_HOST}:{API_PORT}/api/budget"


def fmt_eur(val: float) -> str:
    """Formatiert eine Zahl in deutsches Währungsformat (z.B. 1.234,56 €)."""
    try:
        f = float(val)
        formatted = f"{f:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{formatted} €"
    except Exception:
        return f"{val} €"


class BudgetManagerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Outer Heaven // Budget & Rechnungs-Manager")
        self.geometry("980x860")
        self.minsize(860, 720)

        # Typografische Schriften
        self.font_header = ("DejaVu Sans", 14, "bold")
        self.font_sub = ("DejaVu Sans", 10, "normal")
        self.font_metric_val = ("DejaVu Sans", 18, "bold")
        self.font_metric_lbl = ("DejaVu Sans", 9, "normal")
        self.font_status_offen = ("DejaVu Sans", 10, "normal")
        self.font_status_bezahlt = ("DejaVu Sans", 15, "bold")
        self.font_inv_title = ("DejaVu Sans", 11, "bold")
        self.font_inv_sub = ("DejaVu Sans", 9, "normal")

        # Daten-State
        self.budget_data = {
            "einnahmen": [],
            "fixkosten": [],
            "rechnungen": [],
            "kalkulation": {
                "summe_einnahmen": 0.0,
                "summe_fixkosten": 0.0,
                "summe_rechnungen_offen": 0.0,
                "summe_rechnungen_bezahlt": 0.0,
                "restbudget": 0.0
            }
        }
        self.checkbox_vars = {}
        self.is_loading = False

        self._build_ui()
        self.load_data_async()

    def _build_ui(self):
        # Haupt-Container
        self.main_container = ttk.Frame(self, padding=12)
        self.main_container.pack(fill=tk.BOTH, expand=True)

        # -------------------------------------------------------------
        # 1. OBERER BEREICH: KOPFZEILE & METRIKEN (Einnahmen, Fixkosten, Puffer)
        # -------------------------------------------------------------
        header_frame = ttk.Frame(self.main_container)
        header_frame.pack(fill=tk.X, pady=(0, 10))

        title_lbl = ttk.Label(header_frame, text="BUDGET & LIQUIDITÄTS-MANAGER", font=self.font_header)
        title_lbl.pack(side=tk.LEFT)

        self.btn_refresh = ttk.Button(header_frame, text="↻ Aktualisieren (Fox)", command=self.load_data_async)
        self.btn_refresh.pack(side=tk.RIGHT)

        # Metrik-Karten Frame
        metrics_frame = ttk.LabelFrame(self.main_container, text="Übersicht Monatsbudget & Puffer", padding=10)
        metrics_frame.pack(fill=tk.X, pady=(0, 12))

        # 3 Spalten gleichmäßig verteilen
        metrics_frame.columnconfigure(0, weight=1)
        metrics_frame.columnconfigure(1, weight=1)
        metrics_frame.columnconfigure(2, weight=1)

        # Spalte 1: Einnahmen
        f_inc = ttk.Frame(metrics_frame, padding=8)
        f_inc.grid(row=0, column=0, sticky="nsew", padx=6)
        ttk.Label(f_inc, text="Einnahmen (monatlich)", font=self.font_metric_lbl).pack(anchor="w")
        self.lbl_einnahmen_val = ttk.Label(f_inc, text="0,00 €", font=self.font_metric_val)
        self.lbl_einnahmen_val.pack(anchor="w", pady=(4, 0))

        # Spalte 2: Fixkosten
        f_fix = ttk.Frame(metrics_frame, padding=8)
        f_fix.grid(row=0, column=1, sticky="nsew", padx=6)
        ttk.Label(f_fix, text="Feste Fixkosten", font=self.font_metric_lbl).pack(anchor="w")
        self.lbl_fixkosten_val = ttk.Label(f_fix, text="0,00 €", font=self.font_metric_val)
        self.lbl_fixkosten_val.pack(anchor="w", pady=(4, 0))

        # Spalte 3: Verbleibender Puffer
        f_buf = ttk.Frame(metrics_frame, padding=8)
        f_buf.grid(row=0, column=2, sticky="nsew", padx=6)
        ttk.Label(f_buf, text="Verbleibender Puffer (Restbudget)", font=self.font_metric_lbl).pack(anchor="w")
        self.lbl_puffer_val = ttk.Label(f_buf, text="0,00 €", font=self.font_metric_val)
        self.lbl_puffer_val.pack(anchor="w", pady=(4, 0))
        self.lbl_puffer_sub = ttk.Label(f_buf, text="Offen: 0,00 € | Bezahlt: 0,00 €", font=self.font_sub)
        self.lbl_puffer_sub.pack(anchor="w")

        # -------------------------------------------------------------
        # 2. MITTLERER BEREICH: RECHNUNGEN AUS PAPERLESS (Scrollable)
        # -------------------------------------------------------------
        invoices_group = ttk.LabelFrame(
            self.main_container,
            text="Rechnungen & Zahlungsaufforderungen (Paperless-ngx)",
            padding=8
        )
        invoices_group.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        # Canvas & Scrollbar für Scroll-Bereich
        self.inv_canvas = tk.Canvas(invoices_group, borderwidth=0, highlightthickness=0)
        self.inv_scrollbar = ttk.Scrollbar(invoices_group, orient="vertical", command=self.inv_canvas.yview)
        self.inv_scrollable_frame = ttk.Frame(self.inv_canvas)

        self.inv_scrollable_frame.bind(
            "<Configure>",
            lambda e: self.inv_canvas.configure(scrollregion=self.inv_canvas.bbox("all"))
        )
        self.canvas_window = self.inv_canvas.create_window(
            (0, 0), window=self.inv_scrollable_frame, anchor="nw"
        )
        self.inv_canvas.bind(
            "<Configure>",
            lambda e: self.inv_canvas.itemconfig(self.canvas_window, width=e.width)
        )

        # Mausrad-Unterstützung
        def _on_mousewheel(event):
            self.inv_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.inv_canvas.bind_all("<Button-4>", lambda e: self.inv_canvas.yview_scroll(-1, "units"))
        self.inv_canvas.bind_all("<Button-5>", lambda e: self.inv_canvas.yview_scroll(1, "units"))

        self.inv_canvas.configure(yscrollcommand=self.inv_scrollbar.set)
        self.inv_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.inv_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # -------------------------------------------------------------
        # 3. UNTERER BEREICH: EINGABEFELDER EINNAHMEN & FIXKOSTEN
        # -------------------------------------------------------------
        edit_group = ttk.LabelFrame(self.main_container, text="Monatliche Einnahmen & Fixkosten direkt anpassen", padding=8)
        edit_group.pack(fill=tk.X, pady=(0, 4))
        edit_group.columnconfigure(0, weight=1)
        edit_group.columnconfigure(1, weight=1)

        # Linke Spalte: Einnahmen
        f_edit_inc = ttk.Frame(edit_group, padding=4)
        f_edit_inc.grid(row=0, column=0, sticky="nsew", padx=4)
        ttk.Label(f_edit_inc, text="Einnahmen anpassen:", font=self.font_inv_title).pack(anchor="w", pady=(0, 4))

        self.inc_entries_frame = ttk.Frame(f_edit_inc)
        self.inc_entries_frame.pack(fill=tk.X)

        btn_add_inc = ttk.Button(f_edit_inc, text="+ Einnahme hinzufügen", command=self.add_income_row)
        btn_add_inc.pack(anchor="w", pady=4)

        # Rechte Spalte: Fixkosten
        f_edit_fix = ttk.Frame(edit_group, padding=4)
        f_edit_fix.grid(row=0, column=1, sticky="nsew", padx=4)
        ttk.Label(f_edit_fix, text="Fixkosten anpassen:", font=self.font_inv_title).pack(anchor="w", pady=(0, 4))

        self.fix_entries_frame = ttk.Frame(f_edit_fix)
        self.fix_entries_frame.pack(fill=tk.X)

        btn_add_fix = ttk.Button(f_edit_fix, text="+ Fixkosten hinzufügen", command=self.add_fixed_row)
        btn_add_fix.pack(anchor="w", pady=4)

        # Speicher-Button und Statuszeile
        bottom_actions = ttk.Frame(self.main_container)
        bottom_actions.pack(fill=tk.X, pady=(4, 0))

        self.btn_save = ttk.Button(
            bottom_actions,
            text="💾 Einnahmen & Fixkosten auf Fox speichern",
            command=self.save_budget_async
        )
        self.btn_save.pack(side=tk.LEFT)

        self.lbl_status = ttk.Label(
            bottom_actions,
            text="Bereit. Verbunden mit Fox (100.93.184.87:5005)",
            font=self.font_sub
        )
        self.lbl_status.pack(side=tk.RIGHT, pady=4)

        # Speicher-Listen für Einträge
        self.income_entry_widgets = []
        self.fixed_entry_widgets = []

    # -----------------------------------------------------------------
    # DATEN LADEN & RENDERN
    # -----------------------------------------------------------------
    def load_data_async(self):
        if self.is_loading:
            return
        self.is_loading = True
        self.lbl_status.config(text="Lade Daten von Fox...")
        threading.Thread(target=self._fetch_data_worker, daemon=True).start()

    def _fetch_data_worker(self):
        try:
            resp = requests.get(API_BASE, timeout=6)
            if resp.status_code == 200:
                data = resp.json()
                self.after(0, self._apply_data, data, None)
            else:
                self.after(0, self._apply_data, None, f"HTTP Fehler {resp.status_code}")
        except Exception as e:
            self.after(0, self._apply_data, None, str(e))

    def _apply_data(self, data, error):
        self.is_loading = False
        if error:
            self.lbl_status.config(text=f"Verbindungsfehler: {error}")
            return

        self.budget_data = data
        kalk = data.get("kalkulation", {})

        # 1. Metriken oben aktualisieren
        sum_inc = kalk.get("summe_einnahmen", 0.0)
        sum_fix = kalk.get("summe_fixkosten", 0.0)
        sum_off = kalk.get("summe_rechnungen_offen", 0.0)
        sum_bez = kalk.get("summe_rechnungen_bezahlt", 0.0)
        puffer = kalk.get("restbudget", 0.0)

        self.lbl_einnahmen_val.config(text=fmt_eur(sum_inc))
        self.lbl_fixkosten_val.config(text=fmt_eur(sum_fix))
        self.lbl_puffer_val.config(text=fmt_eur(puffer))
        self.lbl_puffer_sub.config(text=f"Offene Rechnungen: {fmt_eur(sum_off)} | Bereits bezahlt: {fmt_eur(sum_bez)}")

        # 2. Rechnungsliste mitte rendern
        self._render_invoices(data.get("rechnungen", []))

        # 3. Eingabefelder unten aktualisieren
        self._render_input_fields(data.get("einnahmen", []), data.get("fixkosten", []))

        ts = data.get("letzte_aktualisierung", "")
        if "T" in ts:
            ts = ts.split("T")[1][:8]
        self.lbl_status.config(text=f"Stand synchronisiert: {ts} (Fox 100.93.184.87:5005)")

    def _render_invoices(self, invoices):
        # Vorherige Zeilen leeren
        for widget in self.inv_scrollable_frame.winfo_children():
            widget.destroy()

        self.checkbox_vars.clear()

        if not invoices:
            lbl_empty = ttk.Label(self.inv_scrollable_frame, text="Keine Rechnungen in Paperless gefunden.", font=self.font_sub)
            lbl_empty.pack(pady=20)
            return

        for inv in invoices:
            inv_id = inv.get("id")
            title = inv.get("bezeichnung") or inv.get("title", f"Dokument #{inv_id}")
            amount = inv.get("betrag", 0.0)
            due_date = inv.get("faelligkeit", "-")
            status = inv.get("status", "offen")
            is_paid = (status == "bezahlt")

            # Zeilen-Container
            row_frame = ttk.Frame(self.inv_scrollable_frame, padding=6)
            row_frame.pack(fill=tk.X, expand=True, pady=3)

            # Checkbox links zum Umschalten
            var = tk.BooleanVar(value=is_paid)
            self.checkbox_vars[inv_id] = var

            cb = ttk.Checkbutton(
                row_frame,
                variable=var,
                command=lambda i=inv_id, v=var: self.toggle_status_async(i, v)
            )
            cb.pack(side=tk.LEFT, padx=(4, 10))

            # Rechnungsdetails (Mitte)
            info_frame = ttk.Frame(row_frame)
            info_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            lbl_title = ttk.Label(info_frame, text=title, font=self.font_inv_title)
            lbl_title.pack(anchor="w")

            meta_text = f"Dokument-ID: #{inv_id}  |  Fälligkeit: {due_date}  |  Betrag: {fmt_eur(amount)}"
            lbl_sub = ttk.Label(info_frame, text=meta_text, font=self.font_inv_sub)
            lbl_sub.pack(anchor="w")

            # Status-Label (Rechts)
            # REGEL: Status-Unterscheidung rein typografisch!
            # 'offen': normale Schriftgröße, regulärer Text ('Rechnung offen')
            # 'bezahlt': DEUTLICH GRÖSSER und FETT formatiert ('RECHNUNG BEZAHLT')
            status_text = "RECHNUNG BEZAHLT" if is_paid else "Rechnung offen"
            status_font = self.font_status_bezahlt if is_paid else self.font_status_offen

            lbl_status = ttk.Label(
                row_frame,
                text=status_text,
                font=status_font
            )
            lbl_status.pack(side=tk.RIGHT, padx=12)

            # Trennlinie
            sep = ttk.Separator(self.inv_scrollable_frame, orient="horizontal")
            sep.pack(fill=tk.X, padx=4, pady=2)

    def toggle_status_async(self, inv_id, var: tk.BooleanVar):
        target_status = "bezahlt" if var.get() else "offen"
        self.lbl_status.config(text=f"Setze Dokument #{inv_id} auf '{target_status}' in Paperless...")
        threading.Thread(
            target=self._toggle_status_worker,
            args=(inv_id, target_status),
            daemon=True
        ).start()

    def _toggle_status_worker(self, inv_id, target_status):
        try:
            url = f"{API_BASE}/status"
            payload = {"id": inv_id, "status": target_status}
            resp = requests.post(url, json=payload, timeout=7)
            if resp.status_code == 200:
                data = resp.json()
                self.after(0, self._apply_toggle_result, data, None)
            else:
                self.after(0, self._apply_toggle_result, None, f"HTTP Fehler {resp.status_code}")
        except Exception as e:
            self.after(0, self._apply_toggle_result, None, str(e))

    def _apply_toggle_result(self, data, error):
        if error:
            messagebox.showerror("Fehler beim Statuswechsel", f"Konnte Status nicht aktualisieren:\n{error}")
            self.load_data_async()
            return

        # Neu berechnete Rechnungen und Puffer übernehmen
        kalk = data.get("kalkulation", {})
        sum_off = kalk.get("summe_rechnungen_offen", 0.0)
        sum_bez = kalk.get("summe_rechnungen_bezahlt", 0.0)
        puffer = kalk.get("restbudget", 0.0)

        self.lbl_puffer_val.config(text=fmt_eur(puffer))
        self.lbl_puffer_sub.config(text=f"Offene Rechnungen: {fmt_eur(sum_off)} | Bereits bezahlt: {fmt_eur(sum_bez)}")

        # Neu gerenderte Rechnungsliste
        self._render_invoices(data.get("rechnungen", []))
        self.lbl_status.config(text="Status erfolgreich in Paperless-ngx aktualisiert!")

    # -----------------------------------------------------------------
    # EINGABEFELDER EINNAHMEN & FIXKOSTEN
    # -----------------------------------------------------------------
    def _render_input_fields(self, einnahmen, fixkosten):
        for w in self.inc_entries_frame.winfo_children():
            w.destroy()
        for w in self.fix_entries_frame.winfo_children():
            w.destroy()

        self.income_entry_widgets.clear()
        self.fixed_entry_widgets.clear()

        for inc in einnahmen:
            self._add_entry_row(self.inc_entries_frame, self.income_entry_widgets, inc.get("bezeichnung", ""), inc.get("betrag", 0.0))

        for fix in fixkosten:
            self._add_entry_row(self.fix_entries_frame, self.fixed_entry_widgets, fix.get("bezeichnung", ""), fix.get("betrag", 0.0))

    def _add_entry_row(self, parent_frame, target_list, label_text="", amount_val=0.0):
        row = ttk.Frame(parent_frame)
        row.pack(fill=tk.X, pady=2)

        entry_name = ttk.Entry(row, width=28)
        entry_name.insert(0, str(label_text))
        entry_name.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        entry_val = ttk.Entry(row, width=10)
        entry_val.insert(0, f"{float(amount_val):.2f}")
        entry_val.pack(side=tk.LEFT, padx=(0, 4))

        btn_del = ttk.Button(row, text="✕", width=2, command=lambda: self._delete_entry_row(row, target_list, (entry_name, entry_val)))
        btn_del.pack(side=tk.RIGHT)

        target_list.append((entry_name, entry_val))

    def _delete_entry_row(self, row_widget, target_list, pair):
        if pair in target_list:
            target_list.remove(pair)
        row_widget.destroy()

    def add_income_row(self):
        self._add_entry_row(self.inc_entries_frame, self.income_entry_widgets, "Neue Einnahme", 0.0)

    def add_fixed_row(self):
        self._add_entry_row(self.fix_entries_frame, self.fixed_entry_widgets, "Neue Fixkosten", 0.0)

    def save_budget_async(self):
        new_incomes = []
        for idx, (w_name, w_val) in enumerate(self.income_entry_widgets):
            name = w_name.get().strip()
            try:
                val = float(w_val.get().replace(",", "."))
            except ValueError:
                val = 0.0
            if name:
                new_incomes.append({"id": f"e{idx+1}", "bezeichnung": name, "betrag": val, "faelligkeitstag": 1})

        new_fixed = []
        for idx, (w_name, w_val) in enumerate(self.fixed_entry_widgets):
            name = w_name.get().strip()
            try:
                val = float(w_val.get().replace(",", "."))
            except ValueError:
                val = 0.0
            if name:
                new_fixed.append({"id": f"f{idx+1}", "bezeichnung": name, "betrag": val, "faelligkeitstag": 1})

        self.lbl_status.config(text="Speichere Budget-Daten auf Fox...")
        threading.Thread(
            target=self._save_budget_worker,
            args=(new_incomes, new_fixed),
            daemon=True
        ).start()

    def _save_budget_worker(self, new_incomes, new_fixed):
        try:
            url = f"{API_BASE}/update"
            payload = {"einnahmen": new_incomes, "fixkosten": new_fixed}
            resp = requests.post(url, json=payload, timeout=7)
            if resp.status_code == 200:
                data = resp.json()
                self.after(0, self._apply_save_result, data, None)
            else:
                self.after(0, self._apply_save_result, None, f"HTTP Fehler {resp.status_code}")
        except Exception as e:
            self.after(0, self._apply_save_result, None, str(e))

    def _apply_save_result(self, data, error):
        if error:
            messagebox.showerror("Fehler beim Speichern", f"Konnte Daten nicht speichern:\n{error}")
            return
        messagebox.showinfo("Gespeichert", "Budget-Eingaben erfolgreich auf Fox gespeichert!")
        self.load_data_async()


def main():
    app = BudgetManagerGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
