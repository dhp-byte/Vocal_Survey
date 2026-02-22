"""
Application d'enquête vocale interactive avec Streamlit
Basée sur un questionnaire KoboToolBox
Auteur: AI Assistant
Version: 1.0
"""

import streamlit as st
import pandas as pd
import pyttsx3
import speech_recognition as sr
import threading
import time
import json
import sqlite3
from datetime import datetime
import uuid
import re
import os
from pathlib import Path

# Configuration de la page
st.set_page_config(
    page_title="Enquête Vocale Interactive",
    page_icon="🎤",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==================== CONFIGURATION ====================

# Chemin du fichier Excel
EXCEL_FILE = "QUESTIONNAIRE_MONOTORING_2026.xlsx"
DB_FILE = "survey_responses.db"
CSV_FILE = "survey_responses.csv"

# ==================== FONCTIONS UTILITAIRES ====================

def init_database():
    """Initialise la base de données SQLite"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS responses (
            id TEXT PRIMARY KEY,
            timestamp TEXT,
            data TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_to_database(survey_id, data):
    """Sauvegarde les réponses dans SQLite"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO responses (id, timestamp, data) VALUES (?, ?, ?)",
        (survey_id, datetime.now().isoformat(), json.dumps(data))
    )
    conn.commit()
    conn.close()

def save_to_csv(survey_id, data):
    """Sauvegarde les réponses dans un fichier CSV"""
    data_with_meta = {
        'survey_id': survey_id,
        'timestamp': datetime.now().isoformat(),
        **data
    }
    df = pd.DataFrame([data_with_meta])
    
    if os.path.exists(CSV_FILE):
        df.to_csv(CSV_FILE, mode='a', header=False, index=False)
    else:
        df.to_csv(CSV_FILE, mode='w', header=True, index=False)

def load_questionnaire(file_path):
    """Charge et parse le questionnaire KoboToolBox"""
    survey_df = pd.read_excel(file_path, sheet_name='survey')
    choices_df = pd.read_excel(file_path, sheet_name='choices')
    
    # Nettoyer les NaN
    survey_df = survey_df.fillna('')
    choices_df = choices_df.fillna('')
    
    return survey_df, choices_df

def parse_choices(choices_df):
    """Parse les choix et les organise par list_name"""
    choices_dict = {}
    current_list = None
    
    for _, row in choices_df.iterrows():
        list_name = row['list_name']
        if list_name and list_name != '':
            current_list = list_name
            if current_list not in choices_dict:
                choices_dict[current_list] = []
            
            choices_dict[current_list].append({
                'value': str(row['value']),
                'label': row['label'],
                'filter': row.get('filter', '')
            })
    
    return choices_dict

def evaluate_relevant(relevant_expr, responses):
    """Évalue une expression 'relevant' de KoboToolBox"""
    if not relevant_expr or relevant_expr == '':
        return True
    
    try:
        # Remplacer les variables par leurs valeurs
        expr = str(relevant_expr)
        
        # Gérer les expressions simples comme ${variable} = 'value'
        pattern = r'\$\{([^}]+)\}'
        variables = re.findall(pattern, expr)
        
        for var in variables:
            value = responses.get(var, '')
            # Remplacer ${variable} par la valeur
            expr = expr.replace(f'${{{var}}}', f"'{value}'")
        
        # Évaluer l'expression
        result = eval(expr)
        return bool(result)
    except Exception as e:
        # En cas d'erreur, afficher la question par défaut
        return True

def apply_choice_filter(choices, filter_expr, responses):
    """Applique un filtre sur les choix"""
    if not filter_expr or filter_expr == '':
        return choices
    
    try:
        filtered_choices = []
        for choice in choices:
            choice_filter = choice.get('filter', '')
            if choice_filter == '':
                filtered_choices.append(choice)
            else:
                # Évaluer si le choix correspond au filtre
                # Par exemple: filter=${superviseur}
                pattern = r'\$\{([^}]+)\}'
                variables = re.findall(pattern, filter_expr)
                
                match = True
                for var in variables:
                    value = responses.get(var, '')
                    if str(choice_filter) != str(value):
                        match = False
                        break
                
                if match:
                    filtered_choices.append(choice)
        
        return filtered_choices if filtered_choices else choices
    except:
        return choices

# ==================== FONCTIONS VOCALES ====================

class VoiceHandler:
    """Gestionnaire de synthèse et reconnaissance vocale"""
    
    def __init__(self):
        self.engine = None
        self.recognizer = sr.Recognizer()
        self.init_tts()
    
    def init_tts(self):
        """Initialise le moteur de synthèse vocale"""
        try:
            self.engine = pyttsx3.init()
            self.engine.setProperty('rate', 150)  # Vitesse de parole
            self.engine.setProperty('volume', 0.9)
        except Exception as e:
            st.error(f"Erreur d'initialisation TTS: {e}")
    
    def speak(self, text):
        """Lit un texte à haute voix"""
        if self.engine:
            try:
                # Utiliser un thread pour éviter de bloquer l'interface
                def _speak():
                    self.engine.say(text)
                    self.engine.runAndWait()
                
                thread = threading.Thread(target=_speak)
                thread.start()
            except Exception as e:
                st.error(f"Erreur de lecture vocale: {e}")
    
    def listen(self, timeout=5):
        """Écoute et transcrit la voix de l'utilisateur"""
        try:
            with sr.Microphone() as source:
                st.info("🎤 Parlez maintenant...")
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = self.recognizer.listen(source, timeout=timeout)
                
                st.info("⏳ Transcription en cours...")
                text = self.recognizer.recognize_google(audio, language='fr-FR')
                return text
        except sr.WaitTimeoutError:
            st.warning("⏰ Temps d'attente dépassé")
            return None
        except sr.UnknownValueError:
            st.warning("❌ Impossible de comprendre l'audio")
            return None
        except sr.RequestError as e:
            st.error(f"❌ Erreur du service de reconnaissance: {e}")
            return None
        except Exception as e:
            st.error(f"❌ Erreur microphone: {e}")
            return None

# ==================== INTERFACE STREAMLIT ====================

def render_question(question, choices_dict, responses, voice_handler):
    """Affiche une question et gère les réponses"""
    q_type = question['type']
    q_name = question['name']
    q_label = question['label']
    q_hint = question['hint']
    q_required = question['required']
    
    # Afficher le label
    if q_label:
        st.markdown(f"### {q_label}")
        if q_hint:
            st.caption(f"💡 {q_hint}")
    
    # Gestion vocale
    col1, col2 = st.columns([3, 1])
    
    with col2:
        if st.button("🔊 Lire", key=f"read_{q_name}"):
            voice_handler.speak(q_label)
    
    # Gestion selon le type de question
    answer = None
    
    if q_type == 'text':
        col_a, col_b = st.columns([3, 1])
        with col_a:
            answer = st.text_input(
                "Votre réponse",
                value=responses.get(q_name, ''),
                key=f"input_{q_name}",
                label_visibility="collapsed"
            )
        with col_b:
            if st.button("🎤 Vocal", key=f"voice_{q_name}"):
                transcription = voice_handler.listen()
                if transcription:
                    st.success(f"✅ Reconnu: {transcription}")
                    answer = transcription
                    responses[q_name] = answer
                    st.rerun()
    
    elif q_type == 'integer':
        col_a, col_b = st.columns([3, 1])
        with col_a:
            answer = st.number_input(
                "Votre réponse",
                value=int(responses.get(q_name, 0)),
                step=1,
                key=f"input_{q_name}",
                label_visibility="collapsed"
            )
        with col_b:
            if st.button("🎤 Vocal", key=f"voice_{q_name}"):
                transcription = voice_handler.listen()
                if transcription:
                    try:
                        answer = int(transcription)
                        st.success(f"✅ Reconnu: {answer}")
                        responses[q_name] = answer
                        st.rerun()
                    except:
                        st.error("❌ Veuillez dire un nombre")
    
    elif q_type == 'decimal':
        col_a, col_b = st.columns([3, 1])
        with col_a:
            answer = st.number_input(
                "Votre réponse",
                value=float(responses.get(q_name, 0.0)),
                step=0.1,
                key=f"input_{q_name}",
                label_visibility="collapsed"
            )
        with col_b:
            if st.button("🎤 Vocal", key=f"voice_{q_name}"):
                transcription = voice_handler.listen()
                if transcription:
                    try:
                        answer = float(transcription.replace(',', '.'))
                        st.success(f"✅ Reconnu: {answer}")
                        responses[q_name] = answer
                        st.rerun()
                    except:
                        st.error("❌ Veuillez dire un nombre")
    
    elif q_type == 'date':
        answer = st.date_input(
            "Sélectionnez une date",
            value=responses.get(q_name, datetime.now().date()),
            key=f"input_{q_name}",
            label_visibility="collapsed"
        )
        answer = answer.isoformat()
    
    elif q_type.startswith('select_one'):
        # Extraire le nom de la liste
        list_name = q_type.replace('select_one ', '').strip()
        
        if list_name in choices_dict:
            choices = choices_dict[list_name]
            
            # Appliquer le filtre si nécessaire
            choice_filter = question.get('choice_filter', '')
            if choice_filter:
                choices = apply_choice_filter(choices, choice_filter, responses)
            
            # Options pour le selectbox
            options = [''] + [f"{c['value']} - {c['label']}" for c in choices]
            current_value = responses.get(q_name, '')
            
            try:
                current_index = next((i for i, opt in enumerate(options) if opt.startswith(str(current_value))), 0)
            except:
                current_index = 0
            
            selected = st.selectbox(
                "Choisissez une option",
                options=options,
                index=current_index,
                key=f"input_{q_name}",
                label_visibility="collapsed"
            )
            
            if selected and selected != '':
                answer = selected.split(' - ')[0]
    
    elif q_type.startswith('select_multiple'):
        # Extraire le nom de la liste
        list_name = q_type.replace('select_multiple ', '').strip()
        
        if list_name in choices_dict:
            choices = choices_dict[list_name]
            
            # Options pour le multiselect
            options = [f"{c['value']} - {c['label']}" for c in choices]
            current_values = responses.get(q_name, '').split() if responses.get(q_name, '') else []
            current_selection = [opt for opt in options if any(opt.startswith(str(v)) for v in current_values)]
            
            selected = st.multiselect(
                "Choisissez une ou plusieurs options",
                options=options,
                default=current_selection,
                key=f"input_{q_name}",
                label_visibility="collapsed"
            )
            
            if selected:
                answer = ' '.join([s.split(' - ')[0] for s in selected])
    
    elif q_type == 'geopoint':
        st.info("📍 Géolocalisation")
        col1, col2 = st.columns(2)
        with col1:
            lat = st.number_input("Latitude", value=0.0, format="%.6f", key=f"lat_{q_name}")
        with col2:
            lon = st.number_input("Longitude", value=0.0, format="%.6f", key=f"lon_{q_name}")
        answer = f"{lat} {lon} 0 0"
    
    elif q_type == 'note':
        if q_label:
            st.info(q_label)
        answer = None  # Les notes ne sont pas sauvegardées
    
    elif q_type == 'calculate':
        # Les calculs sont effectués automatiquement
        calculation = question.get('calculation', '')
        if calculation:
            try:
                # Évaluer le calcul
                calc_expr = str(calculation)
                pattern = r'\$\{([^}]+)\}'
                variables = re.findall(pattern, calc_expr)
                
                for var in variables:
                    value = responses.get(var, 0)
                    calc_expr = calc_expr.replace(f'${{{var}}}', str(value))
                
                answer = eval(calc_expr)
                st.caption(f"Calculé: {answer}")
            except:
                answer = ''
        else:
            answer = ''
    
    # Validation
    if answer is not None:
        responses[q_name] = answer
    
    return answer

def main():
    """Fonction principale de l'application"""
    
    # Initialisation
    init_database()
    
    # Titre
    st.title("🎤 Enquête Vocale Interactive")
    st.markdown("---")
    
    # Vérifier que le fichier existe
    if not os.path.exists(EXCEL_FILE):
        st.error(f"❌ Fichier {EXCEL_FILE} introuvable!")
        st.info("📁 Veuillez placer le fichier Excel dans le même répertoire que app.py")
        return
    
    # Charger le questionnaire
    try:
        survey_df, choices_df = load_questionnaire(EXCEL_FILE)
        choices_dict = parse_choices(choices_df)
    except Exception as e:
        st.error(f"❌ Erreur de chargement du questionnaire: {e}")
        return
    
    # Initialiser le gestionnaire vocal
    if 'voice_handler' not in st.session_state:
        st.session_state.voice_handler = VoiceHandler()
    
    # Initialiser les réponses
    if 'responses' not in st.session_state:
        st.session_state.responses = {}
        st.session_state.survey_id = str(uuid.uuid4())
        st.session_state.current_question_index = 0
        st.session_state.completed = False
    
    # Filtrer les questions à afficher
    questions = []
    for idx, row in survey_df.iterrows():
        q_type = row['type']
        
        # Ignorer certains types
        if q_type in ['start', 'end', 'begin_group', 'end_group']:
            continue
        
        # Évaluer la pertinence
        relevant = row.get('relevant', '')
        if relevant:
            if not evaluate_relevant(relevant, st.session_state.responses):
                continue
        
        questions.append(row)
    
    # Vérifier si l'enquête est terminée
    if st.session_state.completed:
        st.success("✅ Enquête terminée avec succès!")
        st.balloons()
        
        if st.button("🔄 Nouvelle enquête"):
            st.session_state.responses = {}
            st.session_state.survey_id = str(uuid.uuid4())
            st.session_state.current_question_index = 0
            st.session_state.completed = False
            st.rerun()
        return
    
    # Barre de progression
    if questions:
        progress = st.session_state.current_question_index / len(questions)
        st.progress(progress)
        st.caption(f"Question {st.session_state.current_question_index + 1} / {len(questions)}")
    
    # Afficher la question courante
    if st.session_state.current_question_index < len(questions):
        current_question = questions[st.session_state.current_question_index]
        
        with st.container():
            render_question(
                current_question,
                choices_dict,
                st.session_state.responses,
                st.session_state.voice_handler
            )
        
        # Boutons de navigation
        st.markdown("---")
        col1, col2, col3 = st.columns([1, 1, 1])
        
        with col1:
            if st.session_state.current_question_index > 0:
                if st.button("⬅️ Précédent", use_container_width=True):
                    st.session_state.current_question_index -= 1
                    st.rerun()
        
        with col2:
            pass  # Espace vide
        
        with col3:
            # Vérifier si la question est obligatoire
            is_required = current_question.get('required', '') == 'yes'
            q_name = current_question['name']
            has_answer = q_name in st.session_state.responses and st.session_state.responses[q_name] != ''
            
            if st.session_state.current_question_index < len(questions) - 1:
                if st.button("Suivant ➡️", use_container_width=True):
                    if is_required and not has_answer:
                        st.error("⚠️ Cette question est obligatoire")
                    else:
                        st.session_state.current_question_index += 1
                        st.rerun()
            else:
                if st.button("✅ Soumettre", use_container_width=True):
                    if is_required and not has_answer:
                        st.error("⚠️ Cette question est obligatoire")
                    else:
                        # Sauvegarder les réponses
                        try:
                            save_to_database(st.session_state.survey_id, st.session_state.responses)
                            save_to_csv(st.session_state.survey_id, st.session_state.responses)
                            st.session_state.completed = True
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Erreur de sauvegarde: {e}")
    
    # Sidebar avec informations
    with st.sidebar:
        st.header("📊 Informations")
        st.write(f"**ID Enquête:** {st.session_state.survey_id[:8]}...")
        st.write(f"**Questions répondues:** {len(st.session_state.responses)}")
        st.write(f"**Progression:** {int(progress * 100)}%")
        
        st.markdown("---")
        st.header("🎛️ Paramètres")
        
        if st.button("🔄 Réinitialiser l'enquête"):
            st.session_state.responses = {}
            st.session_state.survey_id = str(uuid.uuid4())
            st.session_state.current_question_index = 0
            st.session_state.completed = False
            st.rerun()
        
        st.markdown("---")
        st.caption("Version 1.0 - Enquête vocale interactive")

if __name__ == "__main__":
    main()
