document.addEventListener('DOMContentLoaded', () => {
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const statusDot = document.getElementById('statusDot');
    const statusMessage = document.getElementById('statusMessage');
    const scannerOverlay = document.getElementById('scannerOverlay');
    const scanFeedback = document.getElementById('scanFeedback');
    const feedbackIcon = document.getElementById('feedbackIcon');
    const feedbackTitle = document.getElementById('feedbackTitle');
    const feedbackText = document.getElementById('feedbackText');
    const manualTokenInput = document.getElementById('manualToken');

    let html5Qrcode = null;
    let isProcessing = false;

    // Check if Html5Qrcode is loaded
    if (typeof Html5Qrcode === 'undefined') {
        statusMessage.innerText = 'Erreur: Bibliothèque de scan introuvable.';
        startBtn.disabled = true;
        return;
    }

    // Initialize scanner instance
    html5Qrcode = new Html5Qrcode("reader");

    startBtn.addEventListener('click', startScanner);
    stopBtn.addEventListener('click', stopScanner);

    function startScanner() {
        startBtn.disabled = true;
        statusMessage.innerText = 'Démarrage de la caméra...';
        
        // Configuration options
        const config = {
            fps: 10,
            qrbox: function(width, height) {
                // Return responsive square box matching layout
                const minEdge = Math.min(width, height);
                const size = Math.floor(minEdge * 0.65);
                return { width: size, height: size };
            },
            aspectRatio: 1.0
        };

        // Use environment (back) camera by default for mobile phone ticket scanning
        html5Qrcode.start(
            { facingMode: "environment" },
            config,
            onScanSuccess,
            onScanFailure
        ).then(() => {
            stopBtn.disabled = false;
            statusDot.classList.add('active');
            statusMessage.innerText = 'Scanner actif - Pointez vers un QR Code';
            scannerOverlay.style.display = 'flex';
        }).catch(err => {
            console.error("Failed to start camera: ", err);
            statusMessage.innerText = 'Impossible d\'activer la caméra. Vérifiez les permissions.';
            startBtn.disabled = false;
        });
    }

    function stopScanner() {
        if (!html5Qrcode) return;
        
        stopBtn.disabled = true;
        statusMessage.innerText = 'Arrêt de la caméra...';
        
        html5Qrcode.stop().then(() => {
            startBtn.disabled = false;
            statusDot.classList.remove('active');
            statusMessage.innerText = 'Caméra inactive';
            scannerOverlay.style.display = 'none';
        }).catch(err => {
            console.error("Failed to stop camera: ", err);
            stopBtn.disabled = false;
        });
    }

    function onScanSuccess(decodedText, decodedResult) {
        // Prevent double processing while showing results
        if (isProcessing) return;
        isProcessing = true;

        // Vibrate if supported
        if (navigator.vibrate) {
            navigator.vibrate(100);
        }

        // Call checkin API
        processCheckin(decodedText);
    }

    function onScanFailure(error) {
        // Quietly fail or log verbose - QR code not found on frames is normal
    }

    function processCheckin(token) {
        statusMessage.innerText = 'Vérification en cours...';

        fetch('/admin/api/checkin', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ token: token })
        })
        .then(response => {
            if (!response.ok) {
                throw new Error('Network response error');
            }
            return response.json();
        })
        .then(data => {
            displayScanResult(data);
        })
        .catch(error => {
            console.error('Scan error:', error);
            displayScanResult({
                status: 'invalid',
                message: 'Erreur réseau ou serveur lors de la validation.'
            });
        });
    }

    function displayScanResult(result) {
        // Clear old feedback classes
        scanFeedback.className = 'scan-feedback';
        
        // Audio & Visual routing based on status
        if (result.status === 'success') {
            playBeep('success');
            scanFeedback.classList.add('scan-feedback-success');
            feedbackIcon.innerText = '✅';
            feedbackTitle.innerText = 'Entrée Validée';
            feedbackText.innerHTML = `<strong>${result.name}</strong><br>Événement : ${result.event}`;
        } else if (result.status === 'already_checked_in') {
            playBeep('warning');
            scanFeedback.classList.add('scan-feedback-warning');
            feedbackIcon.innerText = '⚠️';
            feedbackTitle.innerText = 'Déjà Scanné';
            feedbackText.innerHTML = `<strong>${result.name}</strong><br>Scanné le : ${result.checked_in_at}`;
        } else {
            playBeep('error');
            scanFeedback.classList.add('scan-feedback-error');
            feedbackIcon.innerText = '❌';
            feedbackTitle.innerText = 'Accès Refusé';
            feedbackText.innerText = result.message || 'Ticket non reconnu.';
        }

        // Show feedback overlay
        scanFeedback.classList.add('show');
        statusMessage.innerText = 'Aperçu du résultat...';

        // Clear feedback and release scanner lock after 3 seconds
        setTimeout(() => {
            scanFeedback.classList.remove('show');
            isProcessing = false;
            if (html5Qrcode && html5Qrcode.isScanning) {
                statusMessage.innerText = 'Scanner actif - Pointez vers un QR Code';
            } else {
                statusMessage.innerText = 'Caméra inactive';
            }
        }, 2800);
    }

    function playBeep(type) {
        try {
            const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = audioCtx.createOscillator();
            const gainNode = audioCtx.createGain();
            
            osc.connect(gainNode);
            gainNode.connect(audioCtx.destination);
            
            if (type === 'success') {
                // High-pitched double beep (pleasant chime)
                osc.type = 'sine';
                osc.frequency.setValueAtTime(523.25, audioCtx.currentTime); // C5
                osc.frequency.setValueAtTime(659.25, audioCtx.currentTime + 0.08); // E5
                
                gainNode.gain.setValueAtTime(0.1, audioCtx.currentTime);
                gainNode.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.25);
                
                osc.start();
                osc.stop(audioCtx.currentTime + 0.25);
            } else if (type === 'warning') {
                // Mid warning double beep
                osc.type = 'triangle';
                osc.frequency.setValueAtTime(329.63, audioCtx.currentTime); // E4
                osc.frequency.setValueAtTime(293.66, audioCtx.currentTime + 0.1); // D4
                
                gainNode.gain.setValueAtTime(0.1, audioCtx.currentTime);
                gainNode.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.3);
                
                osc.start();
                osc.stop(audioCtx.currentTime + 0.3);
            } else {
                // Error low buzz
                osc.type = 'sawtooth';
                osc.frequency.setValueAtTime(120, audioCtx.currentTime); // Low buzz
                
                gainNode.gain.setValueAtTime(0.15, audioCtx.currentTime);
                gainNode.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.4);
                
                osc.start();
                osc.stop(audioCtx.currentTime + 0.4);
            }
        } catch (e) {
            console.error('AudioContext synthesis failed: ', e);
        }
    }

    // Manual checkin trigger
    window.validateManualToken = function() {
        const token = manualTokenInput.value.trim();
        if (!token) {
            alert('Veuillez entrer un jeton de validation.');
            return;
        }
        
        if (isProcessing) return;
        isProcessing = true;
        
        processCheckin(token);
        manualTokenInput.value = '';
    };
});
