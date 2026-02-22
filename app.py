"""
Générateur d'Enquête Vocale KoboToolBox - Version Cloud Ready
Compatible Python 3.13+ / Streamlit Cloud
Campagne MILDA 2026 - Vérification 5% des ménages
"""

# ─── Patch de compatibilité Python 3.13 ─────────────────────────────────────
import sys, types

# Patch 'audioop' supprimé en Python 3.13
try:
    import audioop
except ModuleNotFoundError:
    try:
        import audioop_lts as audioop  # pip install audioop-lts
        sys.modules['audioop'] = audioop
    except ModuleNotFoundError:
        # Stub minimal pour éviter les crashes à l'import de speech_recognition
        _stub = types.ModuleType('audioop')
        for _fn in ['max','minmax','avg','avgpp','rms','cross','mul','add',
                    'bias','ulaw2lin','lin2ulaw','alaw2lin','lin2alaw',
                    'lin2lin','adpcm2lin','lin2adpcm','ratecv','reverse',
                    'tomono','tostereo','findfit','findmax','findfactor']:
            setattr(_stub, _fn, lambda *a, **k: (None, None) if _fn in ('ratecv','findfit','findmax') else b'')
        sys.modules['audioop'] = _stub

# Patch 'aifc' supprimé en Python 3.13
try:
    import aifc
except ModuleNotFoundError:
    _aifc_stub = types.ModuleType('aifc')
    sys.modules['aifc'] = _aifc_stub

# Patch 'chunk' supprimé en Python 3.13
try:
    import chunk
except ModuleNotFoundError:
    _chunk_stub = types.ModuleType('chunk')
    sys.modules['chunk'] = _chunk_stub

# ─── Imports Standards ───────────────────────────────────────────────────────
import io, os, re, uuid, sqlite3, logging
import pandas as pd
import streamlit as st
from datetime import datetime
from pathlib import Path

# ─── Imports Optionnels ──────────────────────────────────────────────────────
try:
    from gtts import gTTS
    GTTS_OK = True
except ImportError:
    GTTS_OK = False
    st.warning("⚠️ gTTS non disponible — synthèse vocale désactivée.", icon="🔇")

try:
    from streamlit_mic_recorder import mic_recorder
    MIC_OK = True
except ImportError:
    MIC_OK = False

try:
    import speech_recognition as sr
    SR_OK = True
except (ImportError, Exception):
    SR_OK = False

# ─── Configuration Logging ───────────────────────────────────────────────────
logging.basicConfig(level=logging.WARNING)

# ════════════════════════════════════════════════════════════════════════════
# CONSTANTES
# ════════════════════════════════════════════════════════════════════════════
DB_PATH   = Path("enquetes_milda.db")
CSV_PATH  = Path("enquetes_milda.csv")
XLSX_FILE = "QUESTIONNAIRE_MONOTORING_2026.xlsx"

# ════════════════════════════════════════════════════════════════════════════
# 1. CHARGEMENT DU QUESTIONNAIRE
# ════════════════════════════════════════════════════════════════════════════
@st.cache_data(show_spinner="Chargement du questionnaire…")
def load_questionnaire(path: str):
    """Charge et parse le fichier KoboToolBox XLSForm."""
    survey  = pd.read_excel(path, sheet_name="survey").fillna("")
    choices = pd.read_excel(path, sheet_name="choices").fillna("")

    # Normalisation colonnes survey
    survey.columns = [c.strip().lower().replace(" ", "_") for c in survey.columns]
    choices.columns = [c.strip().lower().replace(" ", "_") for c in choices.columns]

    # Renommage éventuel list name → list_name
    if "list_name" not in choices.columns and "list name" in choices.columns:
        choices = choices.rename(columns={"list name": "list_name"})

    # Nettoyage types
    survey["type"] = survey["type"].astype(str).str.strip()
    survey["name"] = survey["name"].astype(str).str.strip()

    # Dictionnaire choices : list_name → [(value, label), ...]
    choices_dict = {}
    for _, row in choices.iterrows():
        ln = str(row.get("list_name", "")).strip()
        if not ln or ln == "nan":
            continue
        val   = str(row.get("value", row.get("name", ""))).strip().rstrip(".0")
        # Convertir float int (ex: '1.0' → '1')
        try:
            val = str(int(float(val)))
        except (ValueError, TypeError):
            pass
        label = str(row.get("label", "")).strip()
        if ln not in choices_dict:
            choices_dict[ln] = []
        choices_dict[ln].append((val, label))

    return survey, choices_dict


def parse_questions(survey: pd.DataFrame):
    """
    Retourne la liste des questions actives (hors start/end/calculate internes)
    avec leur métadonnées enrichies.
    """
    questions = []
    group_stack = []

    for _, row in survey.iterrows():
        qtype = str(row.get("type", "")).strip()
        qname = str(row.get("name", "")).strip()
        label = str(row.get("label", "")).strip()
        hint  = str(row.get("hint", "")).strip()
        req   = str(row.get("required", "")).strip().lower() in ("yes", "true", "1")
        relev = str(row.get("relevant", "")).strip()
        calc  = str(row.get("calculation", "")).strip()
        constr= str(row.get("constraint", "")).strip()
        chfilt= str(row.get("choice_filter", "")).strip()
        appear= str(row.get("appearance", "")).strip()

        # Groupes
        if qtype in ("begin group", "begin_group"):
            group_stack.append({"name": qname, "relevant": relev})
            continue
        if qtype in ("end group", "end_group", "end_repeat"):
            if group_stack:
                group_stack.pop()
            continue
        if qtype in ("begin_repeat", "begin repeat"):
            group_stack.append({"name": qname, "relevant": relev})
            continue

        # Types ignorés
        if qtype in ("start", "end", "nan", ""):
            continue
        if qname in ("nan", ""):
            continue

        # Type select : extraire list_name
        list_name = ""
        base_type = qtype
        if qtype.startswith("select_one"):
            base_type = "select_one"
            list_name = qtype.replace("select_one", "").strip()
        elif qtype.startswith("select_multiple") or qtype.startswith("select multiple"):
            base_type = "select_multiple"
            list_name = re.sub(r"select[_ ]multiple\s*", "", qtype).strip()

        questions.append({
            "type":       base_type,
            "raw_type":   qtype,
            "name":       qname,
            "label":      label,
            "hint":       hint,
            "required":   req,
            "relevant":   relev,
            "calculation": calc,
            "constraint": constr,
            "choice_filter": chfilt,
            "list_name":  list_name,
            "appearance": appear,
            "group":      group_stack[-1]["name"] if group_stack else None,
            "group_relevant": group_stack[-1]["relevant"] if group_stack else "",
        })

    return questions


# ════════════════════════════════════════════════════════════════════════════
# 2. MOTEUR DE LOGIQUE RELEVANT
# ════════════════════════════════════════════════════════════════════════════
def evaluate_relevant(expr: str, responses: dict) -> bool:
    """
    Évalue les expressions KoboToolBox relevant.
    Ex: ${S1Q01}=1  ou  ${S1Q02}=0  ou  (${A}=1 and ${B}=2)
    """
    if not expr or expr in ("nan", ""):
        return True
    try:
        # Remplacer ${var} par la valeur dans responses
        def replace_var(m):
            varname = m.group(1)
            val = responses.get(varname, "")
            if val == "" or val is None:
                return '""'
            # Numérique ?
            try:
                float(val)
                return str(val)
            except (ValueError, TypeError):
                return f'"{val}"'

        expr2 = re.sub(r"\$\{([^}]+)\}", replace_var, expr)
        # Remplacer opérateurs KoboToolBox
        expr2 = (expr2
                 .replace(" and ", " and ")
                 .replace(" or ",  " or ")
                 .replace("!=", " != ")
                 .replace("=",  " == ")
                 .replace(" ==  == ", " == ")   # fix double ==
                 )
        # Nettoyer doubles ==
        expr2 = re.sub(r'=\s*=\s*=', '==', expr2)
        expr2 = re.sub(r'(?<![=!<>])=(?!=)', '==', expr2)

        return bool(eval(expr2, {"__builtins__": {}}))
    except Exception:
        return True  # En cas d'erreur, on affiche la question


def question_is_visible(q: dict, responses: dict) -> bool:
    """Vérifie la visibilité d'une question (relevant + relevant du groupe)."""
    # Relevant du groupe parent
    if q.get("group_relevant"):
        if not evaluate_relevant(q["group_relevant"], responses):
            return False
    # Relevant propre
    if q.get("relevant"):
        if not evaluate_relevant(q["relevant"], responses):
            return False
    return True


def compute_calculations(questions: list, responses: dict) -> dict:
    """Calcule les champs de type 'calculate'."""
    for q in questions:
        if q["type"] == "calculate" and q.get("calculation"):
            calc = q["calculation"]
            # Substitution simple instance()
            if "instance(" in calc:
                # instance('enqueteur')/root/item[name=${S0Q03}]/label
                m = re.search(r"instance\('([^']+)'\)/root/item\[name=\$\{([^}]+)\}\]/label", calc)
                if m:
                    ref_val = responses.get(m.group(2), "")
                    responses[q["name"]] = ref_val  # sera résolu au rendu
            else:
                try:
                    def repl(match):
                        return responses.get(match.group(1), "")
                    val = re.sub(r"\$\{([^}]+)\}", repl, calc)
                    responses[q["name"]] = val
                except Exception:
                    pass
    return responses


# ════════════════════════════════════════════════════════════════════════════
# 3. SYNTHÈSE VOCALE (gTTS)
# ════════════════════════════════════════════════════════════════════════════
@st.cache_data(show_spinner=False)
def text_to_speech(text: str, lang: str = "fr") -> bytes:
    """Génère un MP3 en mémoire depuis le texte."""
    if not GTTS_OK:
        return b""
    try:
        clean = re.sub(r"\$\{[^}]+\}", "", text).strip()
        clean = re.sub(r"[_\*\#]+", " ", clean)
        if not clean:
            return b""
        tts = gTTS(text=clean, lang=lang, slow=False)
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        return buf.getvalue()
    except Exception as e:
        logging.warning(f"gTTS error: {e}")
        return b""


def play_tts_button(text: str, key: str):
    """Affiche un bouton 🔊 et joue l'audio si cliqué."""
    if not GTTS_OK or not text.strip():
        return
    btn_key = f"tts_{key}"
    if st.button("🔊 Écouter", key=btn_key):
        audio_bytes = text_to_speech(text)
        if audio_bytes:
            st.audio(audio_bytes, format="audio/mp3", autoplay=True)
        else:
            st.warning("Synthèse vocale temporairement indisponible.")


# ════════════════════════════════════════════════════════════════════════════
# 4. RECONNAISSANCE VOCALE (SpeechRecognition)
# ════════════════════════════════════════════════════════════════════════════
def transcribe_audio(audio_bytes: bytes) -> str:
    """Transcrit l'audio en texte via Google Speech Recognition."""
    if not SR_OK:
        return ""
    try:
        recognizer = sr.Recognizer()
        audio_io   = io.BytesIO(audio_bytes)
        with sr.AudioFile(audio_io) as source:
            audio_data = recognizer.record(source)
        return recognizer.recognize_google(audio_data, language="fr-FR")
    except sr.UnknownValueError:
        st.warning("Parole non reconnue — veuillez réessayer.")
        return ""
    except sr.RequestError as e:
        st.error(f"Service Google indisponible: {e}")
        return ""
    except Exception as e:
        logging.warning(f"Transcription error: {e}")
        return ""


def mic_input_widget(label: str, key: str) -> str:
    """Affiche le recorder micro et retourne la transcription."""
    if not MIC_OK:
        st.info("📵 Microphone non disponible — saisie manuelle.")
        return ""
    st.caption("🎙️ Cliquez pour enregistrer votre réponse vocale :")
    audio = mic_recorder(
        start_prompt="🎙️ Démarrer",
        stop_prompt="⏹️ Arrêter",
        key=f"mic_{key}",
        use_container_width=True,
        format="wav",
    )
    if audio and audio.get("bytes"):
        with st.spinner("🔄 Transcription en cours…"):
            text = transcribe_audio(audio["bytes"])
        if text:
            st.success(f"✅ Transcrit : **{text}**")
            return text
    return ""


# ════════════════════════════════════════════════════════════════════════════
# 5. BASE DE DONNÉES SQLITE
# ════════════════════════════════════════════════════════════════════════════
def init_db():
    """Crée la table SQLite si elle n'existe pas."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id          TEXT PRIMARY KEY,
            started_at  TEXT,
            submitted_at TEXT,
            data        TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_submission(session_id: str, started_at: str, responses: dict):
    """Sauvegarde une soumission en JSON dans SQLite."""
    import json
    submitted_at = datetime.utcnow().isoformat()
    data_json    = json.dumps(responses, ensure_ascii=False)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO submissions VALUES (?,?,?,?)",
        (session_id, started_at, submitted_at, data_json)
    )
    conn.commit()
    conn.close()
    _export_csv()
    return submitted_at


def _export_csv():
    """Exporte toutes les soumissions en CSV."""
    import json
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id, started_at, submitted_at, data FROM submissions").fetchall()
    conn.close()
    if not rows:
        return
    records = []
    for sid, sta, sub, dat in rows:
        rec = {"_uuid": sid, "_start": sta, "_submitted": sub}
        try:
            rec.update(json.loads(dat))
        except Exception:
            pass
        records.append(rec)
    pd.DataFrame(records).to_csv(CSV_PATH, index=False, encoding="utf-8-sig")


def get_all_submissions() -> pd.DataFrame:
    """Retourne toutes les soumissions sous forme de DataFrame."""
    import json
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id, started_at, submitted_at, data FROM submissions").fetchall()
    conn.close()
    records = []
    for sid, sta, sub, dat in rows:
        rec = {"UUID": sid, "Début": sta, "Fin": sub}
        try:
            rec.update(json.loads(dat))
        except Exception:
            pass
        records.append(rec)
    return pd.DataFrame(records) if records else pd.DataFrame()


# ════════════════════════════════════════════════════════════════════════════
# 6. RENDU DES QUESTIONS
# ════════════════════════════════════════════════════════════════════════════
def resolve_label(text: str, responses: dict) -> str:
    """Remplace ${var} par la valeur réponse correspondante."""
    def repl(m):
        return responses.get(m.group(1), f"[{m.group(1)}]")
    return re.sub(r"\$\{([^}]+)\}", repl, text)


def render_question(q: dict, responses: dict, choices_dict: dict, idx: int) -> tuple:
    """
    Affiche une question et retourne (value_entered, tts_clicked).
    Retourne (None, False) si la question ne nécessite pas de saisie.
    """
    qtype   = q["type"]
    qname   = q["name"]
    label   = resolve_label(q["label"], responses)
    hint    = resolve_label(q["hint"], responses)
    req_str = " *" if q["required"] else ""
    current = responses.get(qname, "")

    # ── Note / Section ───────────────────────────────────────────────────
    if qtype == "note":
        if label and label not in ("nan", ""):
            st.info(f"ℹ️ {label}", icon="📢")
            play_tts_button(label, f"{qname}_{idx}")
        return None, False

    # ── Champ de saisie ──────────────────────────────────────────────────
    st.markdown(f"### {label}{req_str}")
    if hint and hint not in ("nan", ""):
        st.caption(f"💡 {hint}")

    col_tts, col_mic = st.columns([1, 1])
    with col_tts:
        play_tts_button(label, f"{qname}_{idx}")
    with col_mic:
        if qtype in ("text", "integer", "decimal") and MIC_OK:
            mic_val = mic_input_widget(label, f"{qname}_{idx}")
        else:
            mic_val = ""

    # ── Widgets selon type ───────────────────────────────────────────────
    value = None

    if qtype == "text":
        default = mic_val if mic_val else current
        value = st.text_input(
            label="Réponse :",
            value=default,
            key=f"inp_{qname}_{idx}",
            label_visibility="collapsed",
            placeholder="Saisissez votre réponse…"
        )

    elif qtype == "integer":
        default_i = int(float(current)) if current not in ("", None) else 0
        if mic_val:
            try:
                default_i = int(re.search(r'\d+', mic_val).group())
            except Exception:
                pass
        value = st.number_input(
            "Nombre entier :",
            min_value=0,
            max_value=999999,
            value=default_i,
            step=1,
            key=f"inp_{qname}_{idx}",
            label_visibility="collapsed"
        )
        value = str(value) if value != 0 or current else ""

    elif qtype == "decimal":
        default_d = float(current) if current not in ("", None) else 0.0
        value = st.number_input(
            "Nombre décimal :",
            value=default_d,
            step=0.1,
            key=f"inp_{qname}_{idx}",
            label_visibility="collapsed"
        )
        value = str(value)

    elif qtype == "date":
        try:
            default_d = datetime.strptime(current, "%Y-%m-%d").date() if current else datetime.today().date()
        except Exception:
            default_d = datetime.today().date()
        value = str(st.date_input(
            "Date :",
            value=default_d,
            key=f"inp_{qname}_{idx}",
            label_visibility="collapsed"
        ))

    elif qtype == "select_one":
        ln      = q["list_name"]
        opts    = choices_dict.get(ln, [])
        labels  = [o[1] for o in opts]
        values  = [o[0] for o in opts]
        try:
            default_idx = values.index(str(current)) if current else 0
        except ValueError:
            default_idx = 0
        if labels:
            sel = st.radio(
                "Choix :",
                options=range(len(labels)),
                format_func=lambda i: labels[i],
                index=default_idx,
                key=f"inp_{qname}_{idx}",
                label_visibility="collapsed",
                horizontal=len(labels) <= 4
            )
            value = values[sel]
        else:
            st.warning(f"Liste '{ln}' introuvable.")
            value = st.text_input("Valeur :", value=current, key=f"inp_{qname}_{idx}")

    elif qtype == "select_multiple":
        ln      = q["list_name"]
        opts    = choices_dict.get(ln, [])
        labels  = [o[1] for o in opts]
        values  = [o[0] for o in opts]
        current_list = current.split(" ") if current else []
        defaults = [labels[values.index(v)] for v in current_list if v in values]
        sel = st.multiselect(
            "Choix multiples :",
            options=labels,
            default=defaults,
            key=f"inp_{qname}_{idx}",
            label_visibility="collapsed"
        )
        # Convertir labels → values
        val_map = {lb: vl for vl, lb in zip(values, labels)}
        value   = " ".join([val_map.get(s, s) for s in sel])

    elif qtype == "geopoint":
        st.info("📍 GPS — en environnement cloud, saisissez les coordonnées manuellement.")
        col_lat, col_lon = st.columns(2)
        lat = col_lat.text_input("Latitude", value=current.split()[0] if current else "", key=f"lat_{qname}_{idx}")
        lon = col_lon.text_input("Longitude", value=current.split()[1] if len(current.split()) > 1 else "", key=f"lon_{qname}_{idx}")
        value = f"{lat} {lon}".strip()

    elif qtype == "barcode":
        value = st.text_input(
            "Code QR / Code-barres :",
            value=current,
            key=f"inp_{qname}_{idx}",
            label_visibility="collapsed",
            placeholder="Scannez ou saisissez le code…"
        )

    elif qtype == "image":
        uploaded = st.file_uploader(
            "Photo :",
            type=["jpg","jpeg","png","webp"],
            key=f"img_{qname}_{idx}",
            label_visibility="collapsed"
        )
        if uploaded:
            st.image(uploaded, width=300)
            value = uploaded.name
        else:
            value = current

    elif qtype == "calculate":
        # Invisible, calculé automatiquement
        return None, False

    elif qtype in ("barcode",):
        value = st.text_input("Code :", value=current, key=f"inp_{qname}_{idx}")

    else:
        # Fallback texte
        value = st.text_input(
            f"[{qtype}] Réponse :",
            value=current,
            key=f"inp_{qname}_{idx}",
            label_visibility="collapsed"
        )

    return value, False


# ════════════════════════════════════════════════════════════════════════════
# 7. APPLICATION PRINCIPALE
# ════════════════════════════════════════════════════════════════════════════
def main():
    # ── Config Page ──────────────────────────────────────────────────────────
    st.set_page_config(
        page_title="Enquête MILDA 2026",
        page_icon="🦟",
        layout="centered",
        initial_sidebar_state="expanded",
    )

    # ── CSS ───────────────────────────────────────────────────────────────────
    st.markdown("""
    <style>
    .main-header {
        background: linear-gradient(135deg, #1a6b3c 0%, #2ecc71 100%);
        color: white; padding: 1.2rem 1.5rem; border-radius: 12px;
        margin-bottom: 1.5rem; text-align: center;
    }
    .main-header h1 { color: white; margin: 0; font-size: 1.5rem; }
    .main-header p  { color: rgba(255,255,255,0.85); margin: 0.3rem 0 0; font-size: 0.9rem; }
    .question-card {
        background: #f8fffe; border: 1px solid #d4edda;
        border-left: 5px solid #2ecc71; border-radius: 8px;
        padding: 1.5rem; margin: 1rem 0;
    }
    .progress-info { text-align: right; color: #666; font-size: 0.85rem; }
    .section-badge {
        background: #2ecc71; color: white; border-radius: 20px;
        padding: 0.2rem 0.8rem; font-size: 0.8rem; font-weight: bold;
    }
    .stRadio > div { gap: 0.5rem; }
    div[data-testid="stMetricValue"] { font-size: 1.8rem; color: #1a6b3c; }
    </style>
    """, unsafe_allow_html=True)

    # ── Initialisation ─────────────────────────────────────────────────────
    init_db()

    # ── Chargement questionnaire ───────────────────────────────────────────
    if not Path(XLSX_FILE).exists():
        st.error(f"❌ Fichier introuvable : `{XLSX_FILE}`")
        st.stop()

    survey, choices_dict = load_questionnaire(XLSX_FILE)
    all_questions        = parse_questions(survey)

    # ── Session State ──────────────────────────────────────────────────────
    if "responses"   not in st.session_state: st.session_state.responses   = {}
    if "q_index"     not in st.session_state: st.session_state.q_index     = 0
    if "session_id"  not in st.session_state: st.session_state.session_id  = str(uuid.uuid4())
    if "started_at"  not in st.session_state: st.session_state.started_at  = datetime.utcnow().isoformat()
    if "submitted"   not in st.session_state: st.session_state.submitted   = False
    if "page"        not in st.session_state: st.session_state.page        = "survey"

    responses = st.session_state.responses

    # ── Calcul automatique ─────────────────────────────────────────────────
    compute_calculations(all_questions, responses)

    # ── Questions visibles ────────────────────────────────────────────────
    visible_qs = [q for q in all_questions if question_is_visible(q, responses)]

    # ── Sidebar ───────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### 🦟 MILDA 2026")
        st.markdown("**Vérification 5% des ménages**")
        st.divider()

        page = st.radio(
            "Navigation",
            ["📋 Formulaire", "📊 Données collectées", "ℹ️ À propos"],
            key="nav_page"
        )
        st.divider()

        if visible_qs:
            progress_pct = st.session_state.q_index / len(visible_qs)
            st.progress(progress_pct)
            st.caption(f"Question {min(st.session_state.q_index+1, len(visible_qs))} / {len(visible_qs)}")

        if st.button("🔄 Réinitialiser l'enquête", type="secondary"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

        st.divider()
        st.caption(f"Session: `{st.session_state.session_id[:8]}…`")
        feat_icons = []
        if GTTS_OK: feat_icons.append("🔊 TTS")
        if MIC_OK:  feat_icons.append("🎙️ STT")
        if feat_icons:
            st.caption(" | ".join(feat_icons))

    # ════════════════════════════════════════════════════════════════════
    # PAGE : DONNÉES COLLECTÉES
    # ════════════════════════════════════════════════════════════════════
    if page == "📊 Données collectées":
        st.markdown("""
        <div class="main-header">
            <h1>📊 Données Collectées</h1>
            <p>Toutes les enquêtes soumises</p>
        </div>
        """, unsafe_allow_html=True)

        df = get_all_submissions()
        if df.empty:
            st.info("Aucune soumission pour le moment.")
        else:
            col1, col2 = st.columns(2)
            col1.metric("Total soumissions", len(df))
            col2.metric("Enquêteurs distincts", df.get("S0Q03", pd.Series()).nunique() if "S0Q03" in df.columns else "N/A")
            st.dataframe(df, use_container_width=True)

            if CSV_PATH.exists():
                with open(CSV_PATH, "rb") as f:
                    st.download_button(
                        "⬇️ Télécharger CSV",
                        data=f.read(),
                        file_name=f"enquetes_milda_{datetime.today().strftime('%Y%m%d')}.csv",
                        mime="text/csv",
                        type="primary"
                    )
        return

    # ════════════════════════════════════════════════════════════════════
    # PAGE : À PROPOS
    # ════════════════════════════════════════════════════════════════════
    if page == "ℹ️ À propos":
        st.markdown("""
        <div class="main-header">
            <h1>ℹ️ À propos</h1>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("""
        ### Générateur d'Enquête Vocale KoboToolBox

        **Application** : Enquête de vérification 5% des ménages — Campagne MILDA 2026  
        **Pays** : Tchad — Logone Occidental  
        **Technologie** :
        - 🐍 Python 3.13+ compatible
        - 🌐 Streamlit Cloud Ready
        - 🔊 Synthèse vocale : `gTTS`
        - 🎙️ Reconnaissance vocale : `SpeechRecognition` + `streamlit-mic-recorder`
        - 💾 Stockage : SQLite + export CSV

        **Compatibilité Python 3.13** :
        - Patch `audioop` → `audioop-lts`
        - Stubs `aifc` et `chunk` pour compatibilité
        """)
        return

    # ════════════════════════════════════════════════════════════════════
    # PAGE : FORMULAIRE PRINCIPAL
    # ════════════════════════════════════════════════════════════════════

    # En-tête
    st.markdown("""
    <div class="main-header">
        <h1>🦟 Enquête MILDA 2026</h1>
        <p>Vérification 5% des ménages — Logone Occidental, Tchad</p>
    </div>
    """, unsafe_allow_html=True)

    # Soumission terminée
    if st.session_state.submitted:
        st.balloons()
        st.success("✅ Enquête soumise avec succès !")
        col1, col2 = st.columns(2)
        col1.metric("UUID", st.session_state.session_id[:12] + "…")
        col2.metric("Questions répondues", len([v for v in responses.values() if v]))
        st.json({k: v for k, v in list(responses.items())[:10]})
        if st.button("➕ Nouvelle enquête", type="primary"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()
        return

    if not visible_qs:
        st.warning("Aucune question visible.")
        return

    total_visible = len(visible_qs)
    q_idx         = min(st.session_state.q_index, total_visible - 1)
    q             = visible_qs[q_idx]

    # ── Barre de progression ──────────────────────────────────────────────
    progress_val = (q_idx) / total_visible
    st.progress(progress_val)
    st.markdown(
        f'<div class="progress-info">Question <b>{q_idx + 1}</b> sur <b>{total_visible}</b></div>',
        unsafe_allow_html=True
    )

    # ── Badge section ─────────────────────────────────────────────────────
    section_map = {
        "S0": "Section 0 – Identification", "S1": "Section 1 – Ménage",
        "geo": "Section 2 – Géolocalisation", "info": "Informations"
    }
    sec_prefix = q["name"][:2] if q["name"] != "nan" else ""
    sec_label  = section_map.get(sec_prefix, "")
    if not sec_label and q.get("group"):
        sec_label = f"Groupe : {q['group']}"
    if sec_label:
        st.markdown(f'<span class="section-badge">📁 {sec_label}</span>', unsafe_allow_html=True)
        st.write("")

    # ── Rendu de la question ──────────────────────────────────────────────
    with st.container():
        st.markdown('<div class="question-card">', unsafe_allow_html=True)
        value, _ = render_question(q, responses, choices_dict, q_idx)
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Navigation ────────────────────────────────────────────────────────
    st.write("")
    col_prev, col_spacer, col_next = st.columns([1, 2, 1])

    with col_prev:
        if q_idx > 0:
            if st.button("⬅️ Précédent", use_container_width=True):
                # Sauvegarder la valeur actuelle
                if value is not None and q["type"] not in ("note", "calculate"):
                    st.session_state.responses[q["name"]] = str(value) if value is not None else ""
                st.session_state.q_index = max(0, st.session_state.q_index - 1)
                st.rerun()

    with col_next:
        is_last = (q_idx >= total_visible - 1)
        btn_label = "✅ Soumettre" if is_last else "Suivant ➡️"
        btn_type  = "primary" if is_last else "secondary"

        if st.button(btn_label, type=btn_type, use_container_width=True):
            # Validation
            error = False
            if q["type"] not in ("note", "calculate", "geopoint") and q["required"]:
                if value is None or str(value).strip() in ("", "nan", "0"):
                    st.error(f"⚠️ Ce champ est obligatoire.")
                    error = True

            if not error:
                # Sauvegarder
                if value is not None and q["type"] not in ("note", "calculate"):
                    st.session_state.responses[q["name"]] = str(value) if value is not None else ""

                if is_last:
                    # Soumission finale
                    submitted_at = save_submission(
                        st.session_state.session_id,
                        st.session_state.started_at,
                        st.session_state.responses
                    )
                    st.session_state.submitted = True
                    st.rerun()
                else:
                    st.session_state.q_index += 1
                    st.rerun()

    # ── Aperçu des réponses (pliable) ─────────────────────────────────────
    if responses:
        with st.expander("👁️ Aperçu des réponses saisies", expanded=False):
            clean = {k: v for k, v in responses.items() if v and v not in ("", "nan")}
            if clean:
                for k, v in clean.items():
                    st.write(f"**{k}** → {v}")
            else:
                st.caption("Aucune réponse saisie pour l'instant.")


# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    main()
