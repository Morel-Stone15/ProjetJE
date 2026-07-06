# Diagnostic et solutions — E-mails EmailJS ne fonctionnent pas

## Problème identifié

Il y a **deux causes possibles** pour les e-mails qui ne partent pas :

---

## Cause 1 : Variables manquantes dans le template EmailJS

Votre template `template_oxqnot8` doit contenir **exactement** ces variables (avec les doubles accolades) :

| Variable dans le template | Valeur envoyée |
|---|---|
| `{{to_name}}` | Prénom + Nom du participant |
| `{{to_email}}` | Adresse e-mail du participant |
| `{{event_name}}` | Nom de l'événement |
| `{{event_date}}` | Date de l'événement |
| `{{event_location}}` | Lieu de l'événement |
| `{{ticket_id}}` | Référence du billet (token UUID) |
| `{{qr_code_url}}` | URL directe vers l'image QR code |

**Vérification :**
1. Allez sur [https://dashboard.emailjs.com/admin/templates](https://dashboard.emailjs.com/admin/templates)
2. Cliquez sur `template_oxqnot8`
3. Assurez-vous que le champ **"To Email"** contient `{{to_email}}`
4. Le corps du template doit utiliser ces variables

---

## Cause 2 : API serveur désactivée (pour les envois Flask)

Les e-mails envoyés depuis **Python/Flask** (côté serveur) retournent une erreur 403 car EmailJS bloque par défaut les appels non-navigateur.

**Solution A — Activer l'API serveur (recommandée) :**
1. Allez sur https://dashboard.emailjs.com/admin/account/security
2. Activez **"Allow EmailJS API for non-browser environments"**
3. Redémarrez l'application Flask

**Solution B — Utiliser uniquement le navigateur (déjà en place) :**
Les e-mails de billet et de bienvenue club sont déjà envoyés via le SDK JS dans le navigateur.
Cette approche fonctionne SANS activer l'option serveur.

---

## Comment tester que l'envoi navigateur fonctionne

1. Démarrez l'application : `python app.py`
2. Ouvrez http://localhost:5000
3. Inscrivez-vous à un événement avec votre vraie adresse Gmail
4. Sur la page de confirmation, ouvrez la **Console DevTools** (F12 → Console)
5. Vous devriez voir : `E-mail envoyé avec succès via EmailJS !`
6. Si vous voyez une erreur, elle indiquera le problème exact

---

## Template de bienvenue club — À créer

Si `EMAILJS_WELCOME_TEMPLATE_ID` est vide, l'e-mail de bienvenue ne peut pas être envoyé.

**Créer le template :**
1. https://dashboard.emailjs.com/admin/templates → **Create New Template**
2. To Email : `{{to_email}}`
3. Subject : `Bienvenue sur Campus QR Event !`
4. Corps : utilisez `{{to_name}}`, `{{username}}`, `{{to_email}}`
5. Copiez l'ID du template et mettez-le dans `.env` :
   ```
   EMAILJS_WELCOME_TEMPLATE_ID=template_XXXXXXX
   ```
