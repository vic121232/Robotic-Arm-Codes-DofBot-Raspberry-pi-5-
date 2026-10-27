"""Voice-controlled chess board with a responsive PyQt6 interface.

Install dependencies:
    python3 -m pip install PyQt6 python-chess SpeechRecognition pyaudio

The program starts in listening mode. Say "play chess", then say the source
square and destination square (for example, "e two", then "e four"). Say
"cancel" while a move is being entered to abandon it, or "quit chess" to exit.
"""

import sys
import threading
import chess
import speech_recognition as sr

from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QGridLayout, QLabel, QTextEdit, QFrame
)

# Both white and black pieces use 100% solid-filled Unicode symbols.
PIECE_SYMBOLS = {
    (chess.PAWN, chess.WHITE): "♟",
    (chess.KNIGHT, chess.WHITE): "♞",
    (chess.BISHOP, chess.WHITE): "♝",
    (chess.ROOK, chess.WHITE): "♜",
    (chess.QUEEN, chess.WHITE): "♛",
    (chess.KING, chess.WHITE): "♚",
    (chess.PAWN, chess.BLACK): "♟",
    (chess.KNIGHT, chess.BLACK): "♞",
    (chess.BISHOP, chess.BLACK): "♝",
    (chess.ROOK, chess.BLACK): "♜",
    (chess.QUEEN, chess.BLACK): "♛",
    (chess.KING, chess.BLACK): "♚",
}

LIGHT_SQUARE = "#F2E6D1"
BROWN_SQUARE = "#9A6B46"
SELECTED_SQUARE = "#E8C85A"
WINDOW_BG = "#ECEFF3"
PANEL_BG = "#F6F7F9"
TEXT_DARK = "#182230"


class Communicator(QObject):
    """Thread-safe signal bridge from the background mic worker to the PyQt GUI."""
    sig_event = pyqtSignal(str, str)


class VoiceChessWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voice Chess Assistant - PyQt Edition")
        self.resize(1650, 850)
        self.setMinimumSize(1300, 700)

        self.board = chess.Board()
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True

        self.running = True
        self.command_state = "idle"
        self.pending_from = None

        self.squares = {}
        self.rank_labels = []
        self.file_labels = []

        self.comm = Communicator()
        self.comm.sig_event.connect(self.handle_thread_event)

        self.init_ui()
        self.update_board_display()

        self.log_message("System", "Ready. Say 'play chess' to enter a move.")
        self.log_message("System", "Then say the first square and the destination square.")
        self.log_message("System", "Say 'cancel' to abandon a move, or 'quit chess' to exit.")

        # Start background voice listener thread
        self.voice_thread = threading.Thread(target=self.voice_worker, daemon=True)
        self.voice_thread.start()

    def init_ui(self):
        central_widget = QWidget()
        central_widget.setStyleSheet(f"background-color: {WINDOW_BG};")
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(24)

        # Left side: Chess Board Grid Container
        board_container = QWidget()
        board_layout = QGridLayout(board_container)
        board_layout.setContentsMargins(0, 0, 0, 0)
        board_layout.setSpacing(0)

        self.board_grid_widget = QWidget()
        self.board_grid_widget.setStyleSheet(f"background-color: {WINDOW_BG};")
        grid_layout = QGridLayout(self.board_grid_widget)
        grid_layout.setSpacing(0)
        grid_layout.setContentsMargins(0, 0, 0, 0)

        # Rank labels (8 down to 1)
        for row in range(8):
            rank = 8 - row
            lbl = QLabel(str(rank))
            lbl.setFont(QFont("Arial", 13, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color: {TEXT_DARK}; padding-right: 8px;")
            grid_layout.addWidget(lbl, row, 0)
            self.rank_labels.append(lbl)

            for col in range(8):
                file_index = col
                rank_index = 7 - row
                square = chess.square(file_index, rank_index)
                is_light = (row + col) % 2 == 0
                color = LIGHT_SQUARE if is_light else BROWN_SQUARE

                cell = QLabel("")
                cell.setFont(QFont("DejaVu Sans", 52))
                cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setMinimumSize(85, 85)
                cell.setStyleSheet(f"background-color: {color}; color: {TEXT_DARK};")
                grid_layout.addWidget(cell, row, col + 1)
                self.squares[square] = cell

        # File labels (a through h)
        for col in range(8):
            lbl = QLabel(chess.FILE_NAMES[col])
            lbl.setFont(QFont("Arial", 13, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color: {TEXT_DARK}; padding-top: 6px;")
            grid_layout.addWidget(lbl, 8, col + 1)
            self.file_labels.append(lbl)

        board_layout.addWidget(self.board_grid_widget, 0, 0, Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(board_container, stretch=5)

        # Right side: Voice Command / Chat Panel
        chat_panel = QFrame()
        chat_panel.setStyleSheet(f"background-color: {PANEL_BG}; border: 1px solid #D0D5DC; border-radius: 6px;")
        chat_layout = QVBoxLayout(chat_panel)
        chat_layout.setContentsMargins(16, 16, 16, 16)

        title_lbl = QLabel("Voice Commands")
        title_lbl.setFont(QFont("Arial", 15, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color: {TEXT_DARK}; background: transparent; border: none;")
        chat_layout.addWidget(title_lbl)

        self.chat_log = QTextEdit()
        self.chat_log.setReadOnly(True)
        self.chat_log.setFont(QFont("Menlo", 11))
        self.chat_log.setStyleSheet("background-color: #FFFFFF; color: #182230; border: none; border-radius: 4px;")
        chat_layout.addWidget(self.chat_log)

        # Status frame
        status_frame = QWidget()
        status_frame.setStyleSheet("background-color: #F0F2F5; border-radius: 4px;")
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(12, 12, 12, 12)

        mic_icon = QLabel("🎙")
        mic_icon.setFont(QFont("Arial", 20))
        status_layout.addWidget(mic_icon)

        self.status_label = QLabel("Starting microphone...")
        self.status_label.setFont(QFont("Arial", 11))
        self.status_label.setStyleSheet("color: #4B5563; background: transparent;")
        status_layout.addWidget(self.status_label, stretch=1)

        chat_layout.addWidget(status_frame)
        main_layout.addWidget(chat_panel, stretch=2)

    def log_message(self, sender, text):
        if sender == "System":
            self.chat_log.append(f'<span style="color: #1761B0; font-weight: bold;">System:</span> <span style="color: {TEXT_DARK};">{text}</span>')
        elif sender == "You":
            self.chat_log.append(f'<span style="color: #16833B; font-weight: bold;">You:</span> <span style="color: {TEXT_DARK};">{text}</span>')

    def set_status(self, text, error=False):
        color = "#B42318" if error else "#4B5563"
        self.status_label.setStyleSheet(f"color: {color}; background: transparent;")
        self.status_label.setText(text)

    def update_board_display(self):
        for square, cell in self.squares.items():
            piece = self.board.piece_at(square)
            file_index = chess.square_file(square)
            rank_index = chess.square_rank(square)
            bg_color = LIGHT_SQUARE if (file_index + rank_index) % 2 == 1 else BROWN_SQUARE
            
            if square == self.pending_from:
                bg_color = SELECTED_SQUARE

            if piece is None:
                cell.setText("")
                cell.setStyleSheet(f"background-color: {bg_color}; color: {TEXT_DARK}; min-width: 85px; min-height: 85px;")
            else:
                symbol = PIECE_SYMBOLS[(piece.piece_type, piece.color)]
                # White pieces rendered bright white (#FFFFFF), black pieces deep charcoal (#111111) for solid contrast
                p_color = "#FFFFFF" if piece.color == chess.WHITE else "#111111"
                cell.setText(symbol)
                cell.setStyleSheet(f"background-color: {bg_color}; color: {p_color}; min-width: 85px; min-height: 85px;")

    def handle_thread_event(self, event_type, payload):
        if event_type == "log":
            self.log_message("System", payload)
        elif event_type == "voice":
            self.log_message("You", payload)
            self.handle_voice_command(payload)
            self.set_status("Listening...")
        elif event_type == "notice":
            self.log_message("System", payload)
        elif event_type == "status":
            self.set_status(payload)
        elif event_type == "error":
            self.set_status(payload, error=True)
            self.log_message("System", payload)

    # ------------------------------------------------------------------
    # Voice Worker Thread
    # ------------------------------------------------------------------
    def voice_worker(self):
        try:
            with sr.Microphone() as source:
                self.comm.sig_event.emit("status", "Calibrating microphone...")
                try:
                    self.recognizer.adjust_for_ambient_noise(source, duration=0.6)
                except Exception as exc:
                    self.comm.sig_event.emit("notice", f"Microphone calibration warning: {exc}")

                self.comm.sig_event.emit("status", "Listening...")
                while self.running:
                    try:
                        audio = self.recognizer.listen(
                            source,
                            timeout=1.0,
                            phrase_time_limit=7.0,
                        )
                        spoken_text = self.recognizer.recognize_google(audio).lower().strip()
                        if spoken_text:
                            self.comm.sig_event.emit("voice", spoken_text)
                    except sr.WaitTimeoutError:
                        continue
                    except sr.UnknownValueError:
                        self.comm.sig_event.emit("notice", "Speech was not understood. Please try again.")
                    except sr.RequestError as exc:
                        self.comm.sig_event.emit("error", f"Speech recognition service error: {exc}")
                    except OSError as exc:
                        self.comm.sig_event.emit("error", f"Microphone error: {exc}")
                        break
        except (OSError, AttributeError) as exc:
            self.comm.sig_event.emit("error", f"Could not open the microphone: {exc}")
        except Exception as exc:
            self.comm.sig_event.emit("error", f"Voice input could not start: {exc}")

    # ------------------------------------------------------------------
    # Chess Command Logic State Machine
    # ------------------------------------------------------------------
    def handle_voice_command(self, text):
        text = text.lower().strip()

        if "quit chess" in text or "exit chess" in text:
            self.log_message("System", "Exiting chess program.")
            self.close()
            return

        if self.command_state == "idle":
            if "play chess" in text or "chess move" in text or text == "play":
                self.command_state = "awaiting_from"
                self.pending_from = None
                self.log_message("System", "Chess mode activated. Waiting for first position...")
            return

        if "cancel" in text:
            self.command_state = "idle"
            self.pending_from = None
            self.update_board_display()
            self.log_message("System", "Move cancelled. Board unchanged. Say 'play chess' to start another move.")
            return

        square_name = self.parse_square(text)
        if square_name is None:
            expected = "first" if self.command_state == "awaiting_from" else "second"
            self.log_message("System", f"Could not identify the {expected} square. Say a square such as 'e two'.")
            return

        if self.command_state == "awaiting_from":
            self.pending_from = chess.parse_square(square_name)
            self.command_state = "awaiting_to"
            self.update_board_display()
            self.log_message("System", f"First position received: {square_name}")
            self.log_message("System", "Waiting for second position...")
            return

        if self.command_state == "awaiting_to":
            source = chess.square_name(self.pending_from)
            destination = square_name
            self.log_message("System", f"Second position received: {destination}")
            move = self.find_legal_move(source, destination)

            if move is None:
                self.log_message("System", f"Illegal move: {source} to {destination}. Board unchanged.")
            else:
                san_move = self.board.san(move)
                self.board.push(move)
                self.log_message("System", f"Move executed: {source} -> {destination} ({san_move})")
                self.update_board_display()
                if self.board.is_checkmate():
                    self.log_message("System", "Checkmate. Game over.")
                elif self.board.is_check():
                    self.log_message("System", "Check.")
                elif self.board.is_game_over():
                    self.log_message("System", f"Game over: {self.board.result()}.")
                else:
                    self.log_message("System", "Board updated.")

            self.pending_from = None
            self.command_state = "idle"
            self.update_board_display()
            self.log_message("System", "Ready for the next move. Say 'play chess'.")

    def find_legal_move(self, source_name, destination_name):
        source = chess.parse_square(source_name)
        destination = chess.parse_square(destination_name)

        for move in self.board.legal_moves:
            if move.from_square == source and move.to_square == destination:
                if move.promotion:
                    return chess.Move(source, destination, promotion=chess.QUEEN)
                return move
        return None

    @staticmethod
    def parse_square(text):
        if not text:
            return None
        text = text.lower().strip().replace("-", " ").replace(",", " ")
        if "cancel" in text:
            return "cancel"

        word_map = {
            "one": "1", "won": "1",
            "two": "2", "to": "2", "too": "2",
            "three": "3",
            "four": "4", "for": "4", "fore": "4",
            "five": "5", "six": "6", "seven": "7", "eight": "8",
            "ay": "a", "bee": "b", "be": "b", "see": "c", "sea": "c",
            "dee": "d", "ee": "e", "eff": "f", "gee": "g", "aitch": "h",
        }
        words = [word_map.get(word.strip(".!?"), word.strip(".!?")) for word in text.split()]

        for word in words:
            if len(word) == 2 and word[0] in "abcdefgh" and word[1] in "12345678":
                return word

        for index, word in enumerate(words):
            if word in "abcdefgh" and len(word) == 1:
                for next_word in words[index + 1:]:
                    if next_word in "12345678":
                        return word + next_word
                    if next_word in "abcdefgh":
                        break

        return None

    def closeEvent(self, event):
        self.running = False
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VoiceChessWindow()
    window.show()
    sys.exit(app.exec())