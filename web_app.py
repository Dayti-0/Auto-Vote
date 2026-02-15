"""Interface web Flask pour gerer le Minecraft Auto-Voter."""

import os

import yaml
from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from vote_manager import CONFIG_PATH, VoteManager, load_config, save_config

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Instance globale du gestionnaire de votes
manager = VoteManager()


# ─── Dashboard ────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    config = load_config()
    status = manager.get_status()
    return render_template("dashboard.html", config=config, status=status)


@app.route("/api/status")
def api_status():
    """Endpoint JSON pour le refresh AJAX du dashboard."""
    return jsonify(manager.get_status())


# ─── Gestion des comptes ─────────────────────────────────────────────

@app.route("/accounts")
def accounts():
    config = load_config()
    account_list = config.get("accounts", [])
    return render_template("accounts.html", accounts=account_list, running=manager.is_running)


@app.route("/accounts/add", methods=["POST"])
def add_account():
    pseudo = request.form.get("pseudo", "").strip()
    proxy = request.form.get("proxy", "").strip() or None

    if not pseudo:
        flash("Le pseudo est obligatoire.", "error")
        return redirect(url_for("accounts"))

    if pseudo == "CHANGE_ME":
        flash("Le pseudo ne peut pas etre 'CHANGE_ME'.", "error")
        return redirect(url_for("accounts"))

    config = load_config()
    account_list = config.get("accounts", [])

    # Verifier doublon
    for acc in account_list:
        if acc.get("pseudo") == pseudo:
            flash(f"Le pseudo '{pseudo}' existe deja.", "error")
            return redirect(url_for("accounts"))

    account_list.append({"pseudo": pseudo, "proxy": proxy})
    config["accounts"] = account_list
    save_config(config)

    flash(f"Compte '{pseudo}' ajoute.", "success")
    return redirect(url_for("accounts"))


@app.route("/accounts/edit/<int:index>", methods=["POST"])
def edit_account(index):
    pseudo = request.form.get("pseudo", "").strip()
    proxy = request.form.get("proxy", "").strip() or None

    if not pseudo:
        flash("Le pseudo est obligatoire.", "error")
        return redirect(url_for("accounts"))

    config = load_config()
    account_list = config.get("accounts", [])

    if index < 0 or index >= len(account_list):
        flash("Compte introuvable.", "error")
        return redirect(url_for("accounts"))

    # Verifier doublon (sauf le compte en cours d'edition)
    for i, acc in enumerate(account_list):
        if i != index and acc.get("pseudo") == pseudo:
            flash(f"Le pseudo '{pseudo}' existe deja.", "error")
            return redirect(url_for("accounts"))

    account_list[index] = {"pseudo": pseudo, "proxy": proxy}
    config["accounts"] = account_list
    save_config(config)

    flash(f"Compte '{pseudo}' modifie.", "success")
    return redirect(url_for("accounts"))


@app.route("/accounts/delete/<int:index>", methods=["POST"])
def delete_account(index):
    config = load_config()
    account_list = config.get("accounts", [])

    if index < 0 or index >= len(account_list):
        flash("Compte introuvable.", "error")
        return redirect(url_for("accounts"))

    removed = account_list.pop(index)
    config["accounts"] = account_list
    save_config(config)

    flash(f"Compte '{removed.get('pseudo', '?')}' supprime.", "success")
    return redirect(url_for("accounts"))


# ─── Configuration des sites ─────────────────────────────────────────

@app.route("/settings")
def settings():
    config = load_config()
    return render_template("settings.html", config=config, running=manager.is_running)


@app.route("/settings/save", methods=["POST"])
def save_settings():
    config = load_config()

    # Parametres navigateur
    config["headless"] = request.form.get("headless") == "on"
    config["slow_mo"] = int(request.form.get("slow_mo", 0))
    config["log_level"] = request.form.get("log_level", "INFO")

    # Configuration des sites
    sites = config.get("sites", {})

    for site_key in ["serveur_minecraft_vote", "serveur_prive", "serveur_minecraft"]:
        if site_key not in sites:
            sites[site_key] = {}
        sites[site_key]["enabled"] = request.form.get(f"{site_key}_enabled") == "on"
        sites[site_key]["interval_minutes"] = int(request.form.get(f"{site_key}_interval", 90))
        sites[site_key]["random_delay_max"] = int(request.form.get(f"{site_key}_delay", 0))

    config["sites"] = sites
    save_config(config)

    flash("Configuration sauvegardee.", "success")
    return redirect(url_for("settings"))


# ─── Logs ─────────────────────────────────────────────────────────────

@app.route("/logs")
def logs():
    config = load_config()
    log_file = config.get("log_file", "logs/votes.log")
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), log_file)

    log_lines = []
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            log_lines = f.readlines()[-200:]  # 200 dernieres lignes

    return render_template("logs.html", log_lines=log_lines)


@app.route("/api/logs")
def api_logs():
    """Endpoint JSON pour le refresh AJAX des logs."""
    config = load_config()
    log_file = config.get("log_file", "logs/votes.log")
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), log_file)

    log_lines = []
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            log_lines = f.readlines()[-200:]

    return jsonify({"lines": log_lines})


# ─── Controle du voting ──────────────────────────────────────────────

@app.route("/voting/start", methods=["POST"])
def start_voting():
    if manager.is_running:
        flash("Le voting est deja en cours.", "warning")
    else:
        manager.start()
        flash("Voting demarre.", "success")
    return redirect(url_for("dashboard"))


@app.route("/voting/stop", methods=["POST"])
def stop_voting():
    if not manager.is_running:
        flash("Le voting n'est pas en cours.", "warning")
    else:
        manager.stop()
        flash("Voting arrete.", "success")
    return redirect(url_for("dashboard"))


# ─── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    app.run(host="0.0.0.0", port=5000, debug=True)
