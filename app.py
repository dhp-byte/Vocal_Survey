"""
Application Streamlit - Enquête Vocale Interactive KoboToolBox
Génère une interface vocale web pour formulaires KoboToolBox
Compatible Streamlit Cloud
"""

import streamlit as st
import pandas as pd
import sqlite3
import json
import uuid
from datetime import datetime
import io
import os
import re
from gtts import gTTS
import speech_recognition as sr
from streamlit_mic_recorder import mic_recorder

# Configuration de la page
st.set_page_config(
    page_title="Enquête Vocale - MILDA 2026",
    page_icon="🎤",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# CSS personnalisé pour une interface professionnelle
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        color: #1f77b4;
        text-align: center;
        padding: 1rem;
        background: linear-gradient(90deg, #e3f2fd 0%, #bbdefb 100%);
        border-radius: 10px;
        margin-bottom: 2rem;
    }
    .question-container {
        background-color: #f8f9fa;
        padding: 2rem;
        border-radius: 10px;
        border-left: 5px solid #1f77b4;
        margin: 1rem 0;
    }
    .question-label {
        font-size: 1.5rem;
        font-weight: bold;
        color: #333;
        margin-bottom: 1rem;
    }
    .hint-text {
        font-size: 1rem;
        color: #666;
        font-style: italic;
        margin-bottom: 1rem;
    }
    .progress-container {
        margin: 2rem 0;
    }
    .stButton>button {
        width: 100%;
        padding: 0.75rem;
        font-size: 1.1rem;
        font-weight: bold;
    }
    .required-star {
        color: red;
        font-size: 1.2rem;
    }
</style>
""", unsafe_allow_html=True)

# ============================================================================
# CLASSE PRINCIPALE - GESTIONNAIRE D'ENQUÊTE
# ============================================================================

class KoboSurveyManager:
    """Gestionnaire principal de l'enquête KoboToolBox"""
    
    def __init__(self, excel_file_path):
        self.excel_file = excel_file_path
        self.survey_df = None
        self.choices_df = None
        self.settings_df = None
        self.questions = []
        self.choices_dict = {}
        self.load_data()
        
    def load_data(self):
        """Charge les données du fichier Excel"""
        try:
            self.survey_df = pd.read_excel(self.excel_file, sheet_name='survey')
            self.choices_df = pd.read_excel(self.excel_file, sheet_name='choices')
            self.settings_df = pd.read_excel(self.excel_file, sheet_name='settings')
            
            # Nettoyer les données
            self.survey_df = self.survey_df.fillna('')
            self.choices_df = self.choices_df.fillna('')
            
            # Parser les questions
            self.parse_questions()
            self.parse_choices()
            
        except Exception as e:
            st.error(f"❌ Erreur lors du chargement du fichier : {str(e)}")
            
    def parse_questions(self):
        """Parse les questions de la feuille survey"""
        for idx, row in self.survey_df.iterrows():
            question = {
                'index': idx,
                'type': str(row.get('type', '')),
                'name': str(row.get('name', '')),
                'label': str(row.get('label', '')),
                'hint': str(row.get('hint', '')),
                'required': str(row.get('required', '')).lower() == 'yes',
                'relevant': str(row.get('relevant', '')),
                'calculation': str(row.get('calculation', '')),
                'constraint': str(row.get('constraint', '')),
                'choice_filter': str(row.get('choice_filter', '')),
                'appearance': str(row.get('appearance', ''))
            }
            self.questions.append(question)
            
    def parse_choices(self):
        """Parse les choix de la feuille choices"""
        current_list = None
        
        for idx, row in self.choices_df.iterrows():
            list_name = str(row.get('list_name', '')).strip()
            
            if list_name and list_name != 'nan':
                current_list = list_name
                if current_list not in self.choices_dict:
                    self.choices_dict[current_list] = []
                    
                choice = {
                    'value': str(row.get('value', '')),
                    'label': str(row.get('label', '')),
                    'filter': str(row.get('filter', ''))
                }
                self.choices_dict[current_list].append(choice)
                
    def get_choices_for_question(self, question, filters=None):
        """Récupère les choix pour une question select_one ou select_multiple"""
        q_type = question['type']
        
        # Extraire le nom de la liste de choix
        if 'select_one' in q_type:
            list_name = q_type.replace('select_one', '').strip()
        elif 'select_multiple' in q_type:
            list_name = q_type.replace('select_multiple', '').strip()
        else:
            return []
            
        choices = self.choices_dict.get(list_name, [])
        
        # Appliquer les filtres si nécessaire
        if question['choice_filter'] and filters:
            # TODO: Implémenter la logique de filtrage
            pass
            
        return choices
        
    def evaluate_relevant(self, relevant_expr, responses):
        """Évalue l'expression 'relevant' pour déterminer si une question doit être affichée"""
        if not relevant_expr or relevant_expr == '':
            return True
            
        try:
            # Remplacer les variables ${var} par les valeurs
            expr = relevant_expr
            for var_name, value in responses.items():
                expr = expr.replace(f'${{{var_name}}}', str(value))
                
            # Évaluer l'expression (attention: utiliser avec précaution en production)
            # Pour une version production, utiliser un évaluateur d'expressions sécurisé
            result = eval(expr, {"__builtins__": {}}, {})
            return bool(result)
        except:
            # En cas d'erreur, afficher la question par défaut
            return True
            
    def calculate_value(self, calculation_expr, responses):
        """Calcule une valeur basée sur l'expression 'calculation'"""
        if not calculation_expr or calculation_expr == '':
            return None
            
        try:
            # Pour les calculs simples
            expr = calculation_expr
            for var_name, value in responses.items():
                expr = expr.replace(f'${{{var_name}}}', str(value))
                
            # Gérer les fonctions KoboToolBox spéciales
            if 'instance(' in expr:
                # Fonction instance() pour récupérer des labels
                # TODO: Implémenter la logique complète
                return None
                
            result = eval(expr, {"__builtins__": {}}, {})
            return result
        except:
            return None

# ============================================================================
# FONCTIONS AUDIO
# ============================================================================

def text_to_speech(text, lang='fr'):
    """Convertit le texte en audio avec gTTS et retourne un flux BytesIO"""
    try:
        # Générer l'audio en mémoire
        audio_fp = io.BytesIO()
        tts = gTTS(text=text, lang=lang, slow=False)
        tts.write_to_fp(audio_fp)
        audio_fp.seek(0)
        return audio_fp
    except Exception as e:
        st.error(f"❌ Erreur TTS: {str(e)}")
        return None

def speech_to_text(audio_bytes):
    """Convertit l'audio capturé en texte avec SpeechRecognition"""
    try:
        recognizer = sr.Recognizer()
        
        # Convertir bytes en AudioFile
        audio_file = io.BytesIO(audio_bytes)
        
        with sr.AudioFile(audio_file) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language='fr-FR')
            return text
    except sr.UnknownValueError:
        return "🎤 Désolé, je n'ai pas compris. Veuillez réessayer."
    except sr.RequestError as e:
        return f"❌ Erreur de service: {str(e)}"
    except Exception as e:
        return f"❌ Erreur: {str(e)}"

# ============================================================================
# FONCTIONS DE BASE DE DONNÉES
# ============================================================================

def init_database():
    """Initialise la base de données SQLite"""
    conn = sqlite3.connect('survey_responses.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS responses (
            id TEXT PRIMARY KEY,
            timestamp TEXT,
            data TEXT
        )
    ''')
    
    conn.commit()
    conn.close()

def save_response_to_db(response_id, data):
    """Sauvegarde une réponse dans la base de données"""
    conn = sqlite3.connect('survey_responses.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO responses (id, timestamp, data)
        VALUES (?, ?, ?)
    ''', (response_id, datetime.now().isoformat(), json.dumps(data)))
    
    conn.commit()
    conn.close()

def save_response_to_csv(data):
    """Sauvegarde une réponse dans un fichier CSV"""
    df = pd.DataFrame([data])
    
    if os.path.exists('survey_responses.csv'):
        df.to_csv('survey_responses.csv', mode='a', header=False, index=False)
    else:
        df.to_csv('survey_responses.csv', mode='w', header=True, index=False)

# ============================================================================
# INTERFACE UTILISATEUR
# ============================================================================

def render_question(question, manager, responses):
    """Affiche une question avec interface vocale"""
    
    st.markdown(f"""
    <div class="question-container">
        <div class="question-label">
            {question['label']}
            {'<span class="required-star">*</span>' if question['required'] else ''}
        </div>
        {f'<div class="hint-text">💡 {question["hint"]}</div>' if question['hint'] else ''}
    </div>
    """, unsafe_allow_html=True)
    
    # Synthèse vocale du texte de la question
    col1, col2 = st.columns([3, 1])
    
    with col2:
        if st.button("🔊 Écouter", key=f"listen_{question['name']}"):
            question_text = question['label']
            if question['hint']:
                question_text += f". {question['hint']}"
                
            audio_fp = text_to_speech(question_text)
            if audio_fp:
                st.audio(audio_fp, format='audio/mp3', autoplay=True)
    
    q_type = question['type']
    answer = None
    
    # ========== TYPES DE QUESTIONS ==========
    
    if q_type == 'text':
        # Zone de texte avec option vocale
        col1, col2 = st.columns([3, 1])
        
        with col1:
            answer = st.text_input(
                "Votre réponse:",
                value=responses.get(question['name'], ''),
                key=f"input_{question['name']}",
                label_visibility="collapsed"
            )
        
        with col2:
            st.write("🎤 Réponse vocale:")
            audio = mic_recorder(
                start_prompt="🎤 Démarrer",
                stop_prompt="⏹️ Arrêter",
                key=f"mic_{question['name']}"
            )
            
            if audio:
                transcription = speech_to_text(audio['bytes'])
                st.success(f"Transcription: {transcription}")
                answer = transcription
                
    elif q_type == 'integer':
        answer = st.number_input(
            "Votre réponse:",
            value=int(responses.get(question['name'], 0)),
            step=1,
            key=f"input_{question['name']}",
            label_visibility="collapsed"
        )
        
    elif q_type == 'decimal':
        answer = st.number_input(
            "Votre réponse:",
            value=float(responses.get(question['name'], 0.0)),
            step=0.1,
            key=f"input_{question['name']}",
            label_visibility="collapsed"
        )
        
    elif q_type == 'date':
        answer = st.date_input(
            "Sélectionnez une date:",
            value=datetime.now(),
            key=f"input_{question['name']}",
            label_visibility="collapsed"
        )
        answer = answer.isoformat() if answer else None
        
    elif 'select_one' in q_type:
        choices = manager.get_choices_for_question(question)
        
        if choices:
            options = [c['label'] for c in choices]
            values = [c['value'] for c in choices]
            
            selected = st.radio(
                "Choisissez une option:",
                options,
                key=f"input_{question['name']}",
                label_visibility="collapsed"
            )
            
            # Récupérer la valeur correspondante
            if selected:
                idx = options.index(selected)
                answer = values[idx]
        else:
            st.warning("⚠️ Aucun choix disponible pour cette question")
            
    elif 'select_multiple' in q_type:
        choices = manager.get_choices_for_question(question)
        
        if choices:
            options = [c['label'] for c in choices]
            values = [c['value'] for c in choices]
            
            selected = st.multiselect(
                "Choisissez une ou plusieurs options:",
                options,
                key=f"input_{question['name']}",
                label_visibility="collapsed"
            )
            
            # Récupérer les valeurs correspondantes
            if selected:
                answer = [values[options.index(s)] for s in selected]
            else:
                answer = []
        else:
            st.warning("⚠️ Aucun choix disponible pour cette question")
            
    elif q_type == 'note':
        # Les notes sont juste informatives
        st.info(question['label'])
        answer = None
        
    elif q_type in ['start', 'end']:
        # Horodatages automatiques
        answer = datetime.now().isoformat()
        
    elif q_type == 'calculate':
        # Calculs automatiques
        answer = manager.calculate_value(question['calculation'], responses)
        if answer is not None:
            st.info(f"💻 Valeur calculée: {answer}")
    
    return answer

def main():
    """Fonction principale de l'application"""
    
    # Initialiser la base de données
    init_database()
    
    # En-tête principal
    st.markdown(
        '<div class="main-header">🎤 Enquête Vocale Interactive - MILDA 2026</div>',
        unsafe_allow_html=True
    )
    
    # Vérifier si le fichier Excel existe
    excel_file = 'QUESTIONNAIRE_MONOTORING_2026.xlsx'
    
    if not os.path.exists(excel_file):
        st.error(f"❌ Fichier {excel_file} introuvable. Veuillez uploader le fichier.")
        uploaded_file = st.file_uploader("Uploader le fichier KoboToolBox", type=['xlsx'])
        
        if uploaded_file:
            with open(excel_file, 'wb') as f:
                f.write(uploaded_file.getbuffer())
            st.success("✅ Fichier chargé avec succès!")
            st.rerun()
        return
    
    # Charger le gestionnaire d'enquête
    if 'manager' not in st.session_state:
        st.session_state.manager = KoboSurveyManager(excel_file)
    
    manager = st.session_state.manager
    
    # Initialiser la session
    if 'current_question_idx' not in st.session_state:
        st.session_state.current_question_idx = 0
        st.session_state.responses = {}
        st.session_state.response_id = str(uuid.uuid4())
        st.session_state.start_time = datetime.now().isoformat()
    
    # Afficher les informations du formulaire
    form_title = manager.settings_df.iloc[0]['form_title']
    st.subheader(f"📋 {form_title}")
    
    # Filtrer les questions visibles
    visible_questions = []
    for q in manager.questions:
        if manager.evaluate_relevant(q['relevant'], st.session_state.responses):
            visible_questions.append(q)
    
    total_questions = len(visible_questions)
    current_idx = st.session_state.current_question_idx
    
    # Barre de progression
    if total_questions > 0:
        progress = min(current_idx / total_questions, 1.0)
        st.markdown('<div class="progress-container">', unsafe_allow_html=True)
        st.progress(progress)
        st.write(f"**Question {current_idx + 1} sur {total_questions}**")
        st.markdown('</div>', unsafe_allow_html=True)
    
    # Afficher la question courante
    if current_idx < total_questions:
        current_question = visible_questions[current_idx]
        
        # Afficher la question
        answer = render_question(current_question, manager, st.session_state.responses)
        
        # Sauvegarder la réponse
        if answer is not None:
            st.session_state.responses[current_question['name']] = answer
        
        # Boutons de navigation
        col1, col2, col3 = st.columns([1, 2, 1])
        
        with col1:
            if current_idx > 0:
                if st.button("⬅️ Précédent", use_container_width=True):
                    st.session_state.current_question_idx -= 1
                    st.rerun()
        
        with col3:
            # Vérifier si la question est requise
            can_proceed = True
            if current_question['required']:
                if current_question['name'] not in st.session_state.responses:
                    can_proceed = False
                elif not st.session_state.responses[current_question['name']]:
                    can_proceed = False
            
            if can_proceed:
                if st.button("Suivant ➡️", use_container_width=True, type="primary"):
                    st.session_state.current_question_idx += 1
                    st.rerun()
            else:
                st.button("Suivant ➡️", use_container_width=True, disabled=True)
                st.warning("⚠️ Cette question est obligatoire")
    
    else:
        # Enquête terminée
        st.success("🎉 **Félicitations ! Vous avez terminé l'enquête.**")
        
        # Préparer les données finales
        final_data = {
            'response_id': st.session_state.response_id,
            'start_time': st.session_state.start_time,
            'end_time': datetime.now().isoformat(),
            **st.session_state.responses
        }
        
        # Sauvegarder
        save_response_to_db(st.session_state.response_id, final_data)
        save_response_to_csv(final_data)
        
        st.balloons()
        
        st.write("### 📊 Résumé de vos réponses:")
        st.json(st.session_state.responses)
        
        if st.button("🔄 Recommencer une nouvelle enquête"):
            # Réinitialiser
            st.session_state.current_question_idx = 0
            st.session_state.responses = {}
            st.session_state.response_id = str(uuid.uuid4())
            st.session_state.start_time = datetime.now().isoformat()
            st.rerun()

if __name__ == "__main__":
    main()
