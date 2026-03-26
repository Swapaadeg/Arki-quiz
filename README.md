# Arki-Quizz Bot

Un bot Discord pour lancer des quiz avec thèmes personnalisés, gestion des scores et synchronisation d'inventaire.

## Fonctionnalités

- 📚 **Multiples catégories de quiz** : ARK, Jurassic, Seigneur des Anneaux, Disney, etc.
- 🍓 **Système de points** : Points basés sur la rapidité de réponse
- 📊 **Historique complet** : Sauvegarde tous les quiz avec tous les participants
- 💾 **Synchronisation inventaire** : Envoi automatique des gains vers l'API inventaire
- ⏸️ **Contrôle flux** : Pause, reprise, arrêt des quiz en cours
- 🕐 **Compte à rebours** : Annonces avec timer avant le début

## Installation

### Prérequis
- Python 3.8+
- pip ou venv

### Setup

1. **Cloner le repo**
   ```bash
   git clone <repo-url>
   cd arki-quizz
   ```

2. **Créer un environnement virtuel**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   source .venv/bin/activate  # Linux/Mac
   ```

3. **Installer les dépendances**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configurer les variables d'environnement**
   ```bash
   cp .env.example .env
   # Éditer .env avec vos paramètres
   ```

5. **Lancer le bot**
   ```bash
   python bot.py
   ```

## Configuration (.env)

```env
DISCORD_TOKEN=votre_token
QUIZ_GUILD_ID=id_du_serveur
QUIZ_CHANNEL_ID=id_du_channel_quiz
QUIZ_LAUNCHER_ROLE_ID=id_du_role_launcher
INVENTORY_API_URL=http://api.exemple.com/rewards
INVENTORY_API_KEY=votre_clé_api
```

## Commandes

### Slash Commands (`/`)
- `/quiz <categorie> [nombre] [delai]` - Lance un quiz
- `/pause` - Met en pause/reprend le quiz
- `/stop` - Arrête le quiz en cours
- `/historique [nombre]` - Affiche les derniers quiz
- `/quiz-details <numero>` - Affiche les détails d'un quiz spécifique
- `/resync` - Resynchronise les commandes

### Text Commands (`!`)
- `!quiz <categorie> [nombre] [delai]` - Lance un quiz (alternative)
- `!score` - Affiche ton score
- `!classement` - Affiche le classement global

## Structure

```
arki-quizz/
├── bot.py                 # Bot principal
├── questions.json         # Base de questions
├── quiz_history.json      # Historique des quiz (auto-généré)
├── requirements.txt       # Dépendances Python
├── .env.example           # Exemple de config
├── .gitignore            # Fichiers à ignorer
└── README.md             # Ce fichier
```

## Catégories de Quiz

- **ark** : ARK: Survival Evolved
- **jurassic** : Saga Jurassic Park/World
- **seigneur_des_anneaux** : Le Seigneur des Anneaux
- **disney** : Films Disney
- **test** : Quiz simple pour tests

## Notes

- Les fraises 🍓 gagnées sont synchronisées avec l'API inventaire si configurée
- L'historique est sauvegardé automatiquement après chaque quiz
- Les scores en mémoire sont réinitialisés au redémarrage du bot

## License

MIT
