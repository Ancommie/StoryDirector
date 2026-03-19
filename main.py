
import os
import datetime
import threading
import tkinter as tk
import json
import urllib.request
import urllib.error
import re
import random
from tkinter import messagebox, simpledialog, scrolledtext, font, filedialog

# ==========================================
# Utils & Constants
# ==========================================

# Configuration: Enter your DeepSeek API Key here
DEEPSEEK_API_KEY = ""
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_TOKEN = "---STORY_DIRECTOR_SAVE_STATE---"
SAVE_DELIMITER = "\n" + SAVE_TOKEN + "\n"

SYSTEM_PROMPT = """
You are the Game Master for a text adventure game called "Story Director", designed for Chinese postgraduate English exam (考研英语) candidates.
Your goal is to generate engaging, interactive stories using advanced English (postgraduate exam level, featuring complex sentences, advanced vocabulary, and formal expressions) and RPG elements.

### RESPONSE FORMAT
You must strictly follow this format for every response:

{Story Text}
||
{Stat Changes}
||
{Options}

### SECTIONS DETAILS

1. **Story Text**:
   - 80-150 words.
   - Use advanced vocabulary, complex sentences, and phrasing typical of Chinese postgraduate English exam reading comprehension materials.
   - Descriptive and intellectually engaging.
   - Narrate the outcome of the previous choice and the current situation.

2. **Stat Changes**:
   - Format: `[Stat: Value] [Stat: Value]`
   - Stats available: 
     - `HP` (Health): Physical condition (0-100).
     - `ENG` (Energy): Stamina for actions (0-100). Rest restores it.
     - `LUCK` (Luck): Fortune and chance (0-100). **DO NOT change LUCK unless a special item is found (e.g., [LUCK: +10]). Small fluctuations are handled by the system.**
     - `GOLD` (Gold): Money and wealth. Used to buy items or services.
   - Inventory changes: `[Get: Item Name]` or `[Lost: Item Name]`.
   - Put ALL bracket directives like `[HP: -5]` or `[Get: Item]` ONLY in this section. Do NOT put them in the Story Text section.
   - **CRITICAL INVENTORY RULE**: 
     - ONLY use `[Get: Item]` if the player explicitly accepts or takes the item.
     - If the player refuses, ignores, or consumes an item, DO NOT add it (or use `[Lost: Item]`).
   - Example: `[HP: -5] [GOLD: -10] [Get: Rusty Key]`
   - If no changes, leave this section empty or write `[]`.

3. **Options**:
   - Provide 2 or 3 distinct choices for the player.
   - Format: `Option 1 Text | Option 2 Text | Option 3 Text`
   - Example: `Open the door | Run away | Call for help`

### GAME RULES
- Start with HP: 100, ENG: 100, LUCK: 50, GOLD: 50.
- If HP <= 0, the game ends (Game Over).
- If ENG <= 0, the player passes out (Game Over or heavy penalty).
- LUCK affects the outcome of risky actions.
- GOLD is required for trading.
- Include random events that affect stats.

### EXAMPLE RESPONSE
Navigating the dense, primeval forest, you encounter towering trees that cast an ominous shadow over the path. A sudden, inexplicable noise from behind shatters the silence, reminiscent of a prowling wolf. Despite the overwhelming sense of dread, a faint glimmer of light in the distance offers a beacon of hope.
||
[ENG: -5] [LUCK: -2]
||
Dash toward the distant light | Ascend a nearby tree for safety | Conceal yourself within the dense underbrush
"""

DEFAULT_STATS = {
    "HP": 100,
    "ENG": 100,
    "LUCK": 50,
    "GOLD": 50
}

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

# ==========================================
# Game State Logic
# ==========================================

class GameState:
    def __init__(self):
        self.stats = DEFAULT_STATS.copy()
        self.inventory = []
        self.story_log = [] # List of tuples (role, text)
        self.game_over = False
        self.topic = ""
        self.start_time = None

    def start_new_game(self, topic):
        self.stats = DEFAULT_STATS.copy()
        self.inventory = []
        self.story_log = []
        self.game_over = False
        self.topic = topic
        self.start_time = datetime.datetime.now()
        self.log_story("System", f"Game Started: {topic}")

    def load_from_save(self, data):
        self.stats = data.get("stats", DEFAULT_STATS.copy())
        self.inventory = data.get("inventory", [])
        self.story_log = [(x.get("role", "AI"), x.get("text", "")) for x in data.get("story_log", [])]
        self.game_over = bool(data.get("game_over", False))
        self.topic = data.get("topic", "")
        start_time = data.get("start_time")
        if start_time:
            try:
                self.start_time = datetime.datetime.fromisoformat(start_time)
            except Exception:
                self.start_time = datetime.datetime.now()
        else:
            self.start_time = datetime.datetime.now()

    def to_save(self):
        return {
            "stats": self.stats,
            "inventory": self.inventory,
            "story_log": [{"role": role, "text": text} for role, text in self.story_log],
            "game_over": self.game_over,
            "topic": self.topic,
            "start_time": self.start_time.isoformat() if self.start_time else None
        }

    def repair_inventory_from_history(self):
        inv = []
        for role, text in self.story_log:
            if role != "AI":
                continue
            for key, val in re.findall(r"\[(.*?):(.*?)\]", text or ""):
                k = key.strip()
                v = val.strip()
                if k == "Get":
                    if v and v not in inv:
                        inv.append(v)
                elif k == "Lost":
                    if v in inv:
                        inv.remove(v)
        self.inventory = inv

    def update_state(self, changes):
        """Updates stats and inventory based on AI response."""
        for change in changes:
            key = change['type']
            value = change['value']
            
            if key in self.stats:
                try:
                    # Handle +/- values
                    if value.startswith('+') or value.startswith('-'):
                        val = int(value)
                        
                        # Special logic for LUCK: Only allow large changes (items/events), ignore small ones from AI
                        if key == "LUCK" and abs(val) < 5:
                            pass # Skip small AI adjustments, let random walk handle it
                        else:
                            self.stats[key] += val
                    else:
                        self.stats[key] = int(value)
                    
                    # Cap stats
                    if key in ["HP", "ENG", "LUCK"]:
                        self.stats[key] = min(100, self.stats[key])
                        self.stats[key] = max(0, self.stats[key]) # Ensure non-negative
                    # GOLD can go above 100, no cap needed
                    
                except ValueError:
                    print(f"Error parsing stat value: {key}={value}")
            
            elif key == "Get":
                # Ensure we don't add duplicates if not intended
                if value not in self.inventory:
                    self.inventory.append(value)
            
            elif key == "Lost":
                if value in self.inventory:
                    self.inventory.remove(value)

        luck_change = random.choice([-2, -1, 0, 1, 2])
        self.stats["LUCK"] += luck_change
        self.stats["LUCK"] = max(0, min(100, self.stats["LUCK"]))

        self._check_game_over()

    def _check_game_over(self):
        if self.stats["HP"] <= 0:
            self.game_over = True
            self.log_story("System", "Game Over: Health reached 0.")
        elif self.stats["ENG"] <= 0:
            self.game_over = True
            self.log_story("System", "Game Over: Energy reached 0. You passed out.")

    def log_story(self, role, text):
        self.story_log.append((role, text))

    def export_story(self):
        """Exports the story log to a formatted string."""
        if not self.start_time:
            return "No game played yet."
            
        output = [
            "=== Story Director Record ===",
            f"Topic: {self.topic}",
            f"Date: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            "-----------------------------"
        ]
        
        for role, text in self.story_log:
            output.append(f"[{role}]")
            output.append(text)
            output.append("") # Empty line
            
        return "\n".join(output)

# ==========================================
# AI Engine
# ==========================================

class DeepSeekClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]

    def start_game(self, topic):
        """Starts a new game with the given topic."""
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        user_msg = f"Start a new story about: {topic}"
        return self._send_request(user_msg)

    def make_choice(self, choice_text):
        """Proceeds the story with the user's choice."""
        user_msg = f"I choose: {choice_text}"
        return self._send_request(user_msg)

    def _send_request(self, user_msg):
        """Sends a message to the API and parses the response."""
        self.history.append({"role": "user", "content": user_msg})
        
        if not self.api_key or "sk-" not in self.api_key:
            return {
                "error": "Invalid API Key.",
                "story": "Please configure your DEEPSEEK_API_KEY in the code (main.py).",
                "stats_changes": [],
                "options": ["Quit"]
            }

        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            data = {
                "model": "deepseek-chat",
                "messages": self.history,
                "temperature": 1.2, # Slightly higher for creativity
                "max_tokens": 500
            }
            
            req = urllib.request.Request(DEEPSEEK_API_URL, data=json.dumps(data).encode('utf-8'), headers=headers)
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode('utf-8'))
                content = result['choices'][0]['message']['content']
                
                # Clean up potential markdown code blocks if the model outputs them
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]
                
                content = content.strip()
                
                self.history.append({"role": "assistant", "content": content})
                return self._parse_response(content)
            
        except urllib.error.HTTPError as e:
            return {
                "error": f"HTTP Error: {e.code} - {e.reason}",
                "story": "Error connecting to AI. Please check your API Key.",
                "stats_changes": [],
                "options": ["Try Again", "Quit"]
            }
        except Exception as e:
            return {
                "error": str(e),
                "story": "Error connecting to AI. Please check your internet connection.",
                "stats_changes": [],
                "options": ["Try Again", "Quit"]
            }

    def _parse_response(self, content):
        try:
            raw = (content or "").strip()
            parts = [p.strip() for p in raw.split("||")]

            story_part = ""
            stats_part = ""
            options_part = ""

            if len(parts) >= 3:
                story_part = parts[0]
                stats_part = parts[1]
                options_part = " ".join(parts[2:]).strip()
            elif len(parts) == 2:
                story_part = parts[0]
                if "|" in parts[1]:
                    options_part = parts[1]
                else:
                    stats_part = parts[1]
            else:
                story_part = raw

            extracted_from_story = self._extract_stats(story_part)
            story_clean = re.sub(r"\[[^\[\]]+?:[^\[\]]+?\]", "", story_part).strip()

            stats_changes = []
            stats_changes.extend(self._extract_stats(stats_part))
            stats_changes.extend(extracted_from_story)

            if not options_part:
                for line in reversed(raw.splitlines()):
                    if "|" in line:
                        options_part = line.strip()
                        break

            options = [opt.strip() for opt in options_part.split("|") if opt.strip()]
            if not options:
                options = ["Continue"]

            return {
                "story": story_clean if story_clean else story_part.strip(),
                "stats_changes": stats_changes,
                "options": options
            }
        except Exception as e:
             return {
                "error": f"Parse Error: {str(e)}",
                "story": content, # Fallback to showing full content
                "stats_changes": [],
                "options": ["Continue"]
            }

    def _extract_stats(self, text):
        changes = []
        matches = re.findall(r"\[(.*?):(.*?)\]", text or "")
        for key, val in matches:
            changes.append({"type": key.strip(), "value": val.strip()})
        return changes

# ==========================================
# UI Implementation
# ==========================================

class StoryDirectorUI:
    def __init__(self, root, on_start_game, on_make_choice, on_export, on_quit, on_use_item):
        self.root = root
        self.root.title("Story Director - Postgraduate English Exam Adventure")
        self.root.geometry("1000x700")
        
        # Callbacks
        self.on_start_game = on_start_game
        self.on_make_choice = on_make_choice
        self.on_export = on_export
        self.on_quit = on_quit
        self.on_use_item = on_use_item

        # Fonts
        self.story_font = font.Font(family="Arial", size=18)
        self.ui_font = font.Font(family="Arial", size=14, weight="bold")
        self.choice_font = font.Font(family="Arial", size=15, weight="bold")
        self.user_font = font.Font(family="Arial", size=18, slant="italic")

        self.reading_active = False
        self.pending_options = []
        self.sentence_ranges = []
        self.sentence_index = 0
        self.last_ai_para_start = None
        self.last_ai_para_end = None
        self.ai_para_seq = 0

        self._setup_ui()

    def _setup_ui(self):
        # Configure root window background
        self.root.configure(bg="#f0f2f5")
        
        # 使用 Grid 布局管理全局
        self.root.grid_rowconfigure(0, weight=1) # 故事内容区域
        self.root.grid_rowconfigure(1, weight=0) # 底部按钮区域
        self.root.grid_columnconfigure(0, weight=1) # 左侧故事区
        self.root.grid_columnconfigure(1, weight=0) # 右侧状态栏

        # --- Left Panel: Story Display (Row 0, Col 0) ---
        self.left_panel = tk.Frame(self.root, padx=20, pady=20, bg="#f0f2f5")
        self.left_panel.grid(row=0, column=0, sticky="nsew")
        
        self.story_text = scrolledtext.ScrolledText(
            self.left_panel, 
            font=self.story_font, 
            wrap=tk.WORD, 
            state=tk.NORMAL,
            bg="white",
            fg="#333333",
            bd=0,
            padx=15,
            pady=15,
            relief=tk.FLAT
        )
        self.story_text.bind("<Key>", lambda e: "break")
        self.story_text.tag_config("ai", foreground="#666666", font=self.story_font)
        self.story_text.tag_config("current_sentence", foreground="#000000", background="#fff3b0")
        self.story_text.tag_raise("current_sentence")
        self.story_text.pack(fill=tk.BOTH, expand=True)

        # --- Right Panel: Stats & Inventory (Row 0-1, Col 1) ---
        self.right_panel = tk.Frame(self.root, width=280, bg="#ffffff", padx=15, pady=20, relief=tk.RAISED, bd=1)
        self.right_panel.grid(row=0, column=1, rowspan=2, sticky="ns")
        self.right_panel.pack_propagate(False) # Prevent shrinking

        # Stats
        tk.Label(self.right_panel, text="Character Stats", font=("Arial", 14, "bold"), bg="#ffffff", fg="#2c3e50").pack(pady=(0, 15))
        
        self.lbl_hp = tk.Label(self.right_panel, text="Health: 100", font=self.ui_font, bg="#ffffff", fg="#e74c3c")
        self.lbl_hp.pack(anchor="w", pady=2)
        
        self.lbl_eng = tk.Label(self.right_panel, text="Energy: 100", font=self.ui_font, bg="#ffffff", fg="#f39c12")
        self.lbl_eng.pack(anchor="w", pady=2)
        
        self.lbl_luck = tk.Label(self.right_panel, text="Luck: 50", font=self.ui_font, bg="#ffffff", fg="#9b59b6")
        self.lbl_luck.pack(anchor="w", pady=2)

        self.lbl_gold = tk.Label(self.right_panel, text="Gold: 50", font=self.ui_font, bg="#ffffff", fg="#f1c40f")
        self.lbl_gold.pack(anchor="w", pady=2)

        # Inventory
        tk.Label(self.right_panel, text="Inventory", font=("Arial", 14, "bold"), bg="#ffffff", fg="#2c3e50").pack(pady=(30, 10))
        self.lst_inventory = tk.Listbox(
            self.right_panel, 
            height=15, 
            font=("Arial", 11), 
            bg="#ecf0f1", 
            bd=0, 
            highlightthickness=0,
            selectbackground="#bdc3c7",
            selectforeground="#2c3e50"
        )
        self.lst_inventory.pack(fill=tk.X, padx=5, pady=5)
        self.lst_inventory.bind("<ButtonRelease-1>", self._on_inventory_click)

        # System Buttons
        btn_style = {"font": ("Arial", 10, "bold"), "relief": tk.FLAT, "pady": 5, "cursor": "hand2"}
        tk.Button(self.right_panel, text="Export Story", command=self.on_export, bg="#3498db", fg="white", **btn_style).pack(fill=tk.X, pady=(30, 5))
        tk.Button(self.right_panel, text="New Game", command=self.ask_new_game, bg="#2ecc71", fg="white", **btn_style).pack(fill=tk.X, pady=5)
        tk.Button(self.right_panel, text="Quit", command=self.on_quit, bg="#e74c3c", fg="white", **btn_style).pack(fill=tk.X, pady=5)

        # --- Bottom Panel: Choices (Row 1, Col 0) ---
        self.bottom_panel = tk.Frame(self.root, padx=20, pady=20, bg="#f0f2f5")
        self.bottom_panel.grid(row=1, column=0, sticky="ew")
        
        self.read_ctrl = tk.Frame(self.bottom_panel, bg="#f0f2f5")
        self.read_ctrl.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))
        self.btn_prev_sentence = tk.Button(self.read_ctrl, text="上一句", font=("Arial", 12, "bold"), relief=tk.FLAT, bg="#bdc3c7", fg="#2c3e50", cursor="hand2", command=self.prev_sentence)
        self.btn_prev_sentence.pack(side=tk.LEFT, padx=10)
        self.lbl_sentence_progress = tk.Label(self.read_ctrl, text="", font=("Arial", 12, "bold"), bg="#f0f2f5", fg="#2c3e50")
        self.lbl_sentence_progress.pack(side=tk.LEFT, expand=True)
        self.btn_next_sentence = tk.Button(self.read_ctrl, text="下一句", font=("Arial", 12, "bold"), relief=tk.FLAT, bg="#3498db", fg="white", cursor="hand2", command=self.next_sentence)
        self.btn_next_sentence.pack(side=tk.RIGHT, padx=10)
        self.read_ctrl.pack_forget()

        self.btn_container = tk.Frame(self.bottom_panel, bg="#f0f2f5")
        self.btn_container.pack(expand=True, fill=tk.X)

        self.choice_buttons = []
        for i in range(3):
            btn = tk.Button(
                self.btn_container, 
                text=f"Option {i+1}", 
                font=self.choice_font, 
                wraplength=800,
                command=lambda idx=i: self._handle_choice_click(idx),
                bg="white",
                fg="#2c3e50",
                relief=tk.RAISED,
                bd=2,
                activebackground="#ecf0f1",
                cursor="hand2"
            )
            # Initial pack
            btn.pack(side=tk.TOP, fill=tk.X, expand=True, pady=8, padx=100)
            self.choice_buttons.append(btn)
        
        # Hide buttons initially
        self.update_choices([])

    def update_display(self, text, role="AI"):
        if role == "System":
            self.story_text.insert(tk.END, f"\n--- {text} ---\n\n", "system")
            self.story_text.tag_config("system", foreground="gray", justify="center", font=self.story_font)
        elif role == "User":
            self.story_text.insert(tk.END, f"\n> You: {text}\n\n", "user")
            self.story_text.tag_config("user", foreground="blue", font=self.user_font)
        else:
            self.story_text.insert(tk.END, f"{text}\n", "ai")
        
        self.story_text.see(tk.END)

    def display_ai_paragraph_with_reader(self, paragraph_text, options):
        self.pending_options = options or []
        self.reading_active = True
        self.sentence_ranges = []
        self.sentence_index = 0

        self.story_text.tag_remove("current_sentence", "1.0", tk.END)
        self.ai_para_seq += 1
        para_tag = f"ai_para_{self.ai_para_seq}"
        insert_text = f"{paragraph_text}\n\n"
        self.story_text.insert(tk.END, insert_text, ("ai", para_tag))
        ranges = self.story_text.tag_ranges(para_tag)
        if len(ranges) >= 2:
            para_start = ranges[0]
            para_end = ranges[1]
        else:
            para_start = self.story_text.index(tk.END)
            para_end = self.story_text.index(tk.END)
        self.story_text.see(tk.END)

        self.last_ai_para_start = para_start
        self.last_ai_para_end = para_end

        spans = []
        start = 0
        i = 0
        n = len(paragraph_text)
        while i < n:
            ch = paragraph_text[i]
            if ch in ".!?":
                end = i + 1
                while end < n and paragraph_text[end] in "\"'”’":
                    end += 1
                s = start
                e = end
                while s < e and paragraph_text[s].isspace():
                    s += 1
                while e > s and paragraph_text[e - 1].isspace():
                    e -= 1
                if e > s:
                    spans.append((s, e))
                start = end
                i = end
                continue
            i += 1
        if start < n:
            s = start
            e = n
            while s < e and paragraph_text[s].isspace():
                s += 1
            while e > s and paragraph_text[e - 1].isspace():
                e -= 1
            if e > s:
                spans.append((s, e))
        if not spans:
            spans = [(0, len(paragraph_text))]

        for s, e in spans:
            start_idx = f"{para_start}+{s}c"
            end_idx = f"{para_start}+{e}c"
            self.sentence_ranges.append((start_idx, end_idx))

        self.update_choices([])
        self.read_ctrl.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))
        self._apply_sentence_highlight()

    def _apply_sentence_highlight(self):
        total = len(self.sentence_ranges)
        if total == 0:
            self.reading_active = False
            self.read_ctrl.pack_forget()
            self.update_choices(self.pending_options)
            return

        self.sentence_index = max(0, min(total - 1, self.sentence_index))
        start_idx, end_idx = self.sentence_ranges[self.sentence_index]
        self.story_text.tag_remove("current_sentence", "1.0", tk.END)
        self.story_text.tag_add("current_sentence", start_idx, end_idx)
        self.story_text.tag_raise("current_sentence")
        self.story_text.see(start_idx)

        self.btn_prev_sentence.config(state=tk.NORMAL if self.sentence_index > 0 else tk.DISABLED)
        if self.sentence_index >= total - 1:
            self.btn_next_sentence.config(text="显示选项")
        else:
            self.btn_next_sentence.config(text="下一句")
        self.lbl_sentence_progress.config(text=f"{self.sentence_index + 1} / {total}")

    def prev_sentence(self):
        if not self.reading_active:
            return
        if self.sentence_index > 0:
            self.sentence_index -= 1
            self._apply_sentence_highlight()

    def next_sentence(self):
        if not self.reading_active:
            return
        if self.sentence_index >= len(self.sentence_ranges) - 1:
            self.end_reading_mode(show_options=True)
        else:
            self.sentence_index += 1
            self._apply_sentence_highlight()

    def end_reading_mode(self, show_options=True):
        self.reading_active = False
        self.story_text.tag_remove("current_sentence", "1.0", tk.END)
        self.read_ctrl.pack_forget()
        if show_options:
            self.update_choices(self.pending_options)
        else:
            self.update_choices([])

    def update_stats(self, stats, inventory):
        self.lbl_hp.config(text=f"Health: {stats.get('HP', 0)}")
        self.lbl_eng.config(text=f"Energy: {stats.get('ENG', 0)}")
        self.lbl_luck.config(text=f"Luck: {stats.get('LUCK', 50)}")
        self.lbl_gold.config(text=f"Gold: {stats.get('GOLD', 50)}")
        
        self.lst_inventory.delete(0, tk.END)
        for item in inventory:
            self.lst_inventory.insert(tk.END, item)

    def update_choices(self, options):
        # Clear existing commands
        for btn in self.choice_buttons:
            btn.pack_forget()
            
        self.current_options = options
        
        if not options:
            return

        for i, option_text in enumerate(options):
            if i < len(self.choice_buttons):
                btn = self.choice_buttons[i]
                # Reset command to ensure correct handler is used (important after Game Over overrides)
                btn.config(text=option_text, state=tk.NORMAL, command=lambda idx=i: self._handle_choice_click(idx))
                btn.pack(side=tk.TOP, fill=tk.X, expand=True, pady=8, padx=100)

    def _handle_choice_click(self, index):
        if 0 <= index < len(self.current_options):
            choice = self.current_options[index]
            self.on_make_choice(choice)

    def ask_api_key(self):
        return simpledialog.askstring("API Key", "Please enter your DeepSeek API Key:", show="*")

    def ask_new_game(self):
        topic = simpledialog.askstring("New Game", "Enter a topic for your story (e.g., 'Magic School'):")
        if topic:
            self.on_start_game(topic)

    def show_message(self, title, message):
        messagebox.showinfo(title, message)
    
    def show_error(self, message):
        messagebox.showerror("Error", message)

    def disable_choices(self):
        for btn in self.choice_buttons:
            btn.config(state=tk.DISABLED)

    def _on_inventory_click(self, event):
        selection = self.lst_inventory.curselection()
        if not selection:
            return
        idx = selection[0]
        item = self.lst_inventory.get(idx)
        self._show_item_popup(item)

    def _item_description(self, item):
        lower = item.lower()
        if "tea" in lower:
            return "A warm cup of tea. It may help you relax and recover energy."
        if "potion" in lower:
            return "A small potion. It may restore your health."
        if "key" in lower:
            return "A key. It may open a locked door or box."
        if "map" in lower:
            return "A map. It may help you avoid danger and find treasure."
        if "coin" in lower or "gold" in lower:
            return "Coins. Useful for buying items or paying for help."
        return "A useful item. You can try to use it at a good moment."

    def _show_item_popup(self, item):
        win = tk.Toplevel(self.root)
        win.title(item)
        win.geometry("420x240")
        win.configure(bg="#ffffff")
        lbl_name = tk.Label(win, text=item, font=("Arial", 14, "bold"), bg="#ffffff", fg="#2c3e50")
        lbl_name.pack(pady=(15, 10))
        lbl_desc = tk.Label(win, text=self._item_description(item), font=("Arial", 12), bg="#ffffff", fg="#333333", wraplength=380, justify="left")
        lbl_desc.pack(padx=20, pady=(0, 15))
        btn_row = tk.Frame(win, bg="#ffffff")
        btn_row.pack(pady=10, fill=tk.X)
        btn_use = tk.Button(btn_row, text="Use", font=("Arial", 12, "bold"), relief=tk.FLAT, bg="#2ecc71", fg="white", cursor="hand2", command=lambda: (win.destroy(), self.on_use_item(item)))
        btn_use.pack(side=tk.LEFT, expand=True, padx=20, ipadx=10, ipady=6)
        btn_cancel = tk.Button(btn_row, text="Cancel", font=("Arial", 12, "bold"), relief=tk.FLAT, bg="#bdc3c7", fg="#2c3e50", cursor="hand2", command=win.destroy)
        btn_cancel.pack(side=tk.RIGHT, expand=True, padx=20, ipadx=10, ipady=6)

# ==========================================
# Main App
# ==========================================

class StoryDirectorApp:
    def __init__(self, root):
        self.root = root
        self.game_state = GameState()
        self.ai_client = None
        self.last_options = []
        
        # Initialize UI
        self.ui = StoryDirectorUI(
            root,
            on_start_game=self.start_game,
            on_make_choice=self.make_choice,
            on_export=self.export_story,
            on_quit=root.quit,
            on_use_item=self.use_item
        )
        
        # Delay API key prompt until UI is shown
        self.root.after(100, self.initialize_ai)

    def initialize_ai(self):
        # Use the global configuration variable directly
        api_key = DEEPSEEK_API_KEY
        
        # If the key is still the placeholder, ask the user
        if not api_key or "sk-" not in api_key:
             api_key = self.ui.ask_api_key()

        if not api_key:
            self.ui.show_error("API Key is required to play. Exiting.")
            self.root.quit()
            return

        self.ai_client = DeepSeekClient(api_key)
        self._startup_flow()

    def _startup_flow(self):
        answer = messagebox.askyesnocancel("Story Director", "Load a saved game from this folder?\nYes: Load\nNo: New Game")
        if answer is None:
            self.root.quit()
            return
        if answer:
            file_path = filedialog.askopenfilename(initialdir=SCRIPT_DIR, title="Open Save File", filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
            if file_path:
                ok = self.load_game(file_path)
                if ok:
                    return
        self.ui.ask_new_game()

    def start_game(self, topic):
        self.game_state.start_new_game(topic)
        self.ui.story_text.delete(1.0, tk.END) # Clear text
        
        self.ui.update_display(f"Starting new story about: {topic}...", "System")
        self.ui.disable_choices()
        
        # Run AI in background thread to avoid freezing UI
        threading.Thread(target=self._ai_start_turn, args=(topic,), daemon=True).start()

    def _ai_start_turn(self, topic):
        response = self.ai_client.start_game(topic)
        self.root.after(0, self._handle_ai_response, response)

    def make_choice(self, choice_text):
        self.ui.end_reading_mode(show_options=False)
        self.game_state.log_story("User", choice_text)
        self.ui.update_display(choice_text, "User")
        self.ui.disable_choices()
        
        threading.Thread(target=self._ai_next_turn, args=(choice_text,), daemon=True).start()

    def _ai_next_turn(self, choice_text):
        response = self.ai_client.make_choice(choice_text)
        self.root.after(0, self._handle_ai_response, response)

    def _handle_ai_response(self, response):
        if "error" in response:
            self.ui.show_error(response["error"])
        
        # 1. Update Story
        story_text = response.get("story", "")
        self.game_state.log_story("AI", story_text)
        
        # 2. Update Stats
        changes = response.get("stats_changes", [])
        self.game_state.update_state(changes)
        self.ui.update_stats(self.game_state.stats, self.game_state.inventory)
        
        # 3. Check Game Over
        if self.game_state.game_over:
            self.ui.end_reading_mode(show_options=False)
            self.ui.update_display(story_text, "AI")
            self.ui.update_choices(["Restart", "Quit"])
            self.ui.choice_buttons[0].config(command=self.ui.ask_new_game)
            self.ui.choice_buttons[1].config(command=self.root.quit)
        else:
            # 4. Update Options
            options = response.get("options", [])
            self.last_options = options
            self.ui.display_ai_paragraph_with_reader(story_text, options)

    def export_story(self):
        content = self.game_state.export_story()
        save_data = self.game_state.to_save()
        save_data["ai_history"] = getattr(self.ai_client, "history", [])
        save_data["options"] = self.ui.pending_options if getattr(self.ui, "reading_active", False) else self.last_options
        filename = os.path.join(SCRIPT_DIR, "story_export.txt")
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
            f.write(SAVE_DELIMITER)
            f.write(json.dumps(save_data, ensure_ascii=False))
        self.ui.show_message("Export", f"Story saved to {filename}")

    def load_game(self, file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                txt = f.read()
            if SAVE_TOKEN not in txt:
                self.ui.show_error("This file does not contain save state.")
                return False
            idx = txt.find(SAVE_TOKEN)
            state_part = txt[idx + len(SAVE_TOKEN):]
            state = json.loads(state_part.strip())
            self.game_state.load_from_save(state)
            if "ai_history" in state:
                self.ai_client.history = state["ai_history"]
            self.last_options = state.get("options", [])
            if not self.game_state.inventory:
                self.game_state.repair_inventory_from_history()
            if self.last_options == ["Continue"]:
                last_ai = ""
                for role, text in reversed(self.game_state.story_log):
                    if role == "AI":
                        last_ai = text
                        break
                if "|" in last_ai:
                    last_line = ""
                    for line in reversed(last_ai.splitlines()):
                        if "|" in line:
                            last_line = line.strip()
                            break
                    if last_line:
                        opts = [x.strip() for x in last_line.split("|") if x.strip()]
                        if opts:
                            self.last_options = opts

            self.ui.story_text.delete(1.0, tk.END)
            self.ui.end_reading_mode(show_options=False)

            for role, text in self.game_state.story_log:
                self.ui.update_display(text, role)
            self.ui.update_stats(self.game_state.stats, self.game_state.inventory)
            if self.game_state.game_over:
                self.ui.update_choices(["Restart", "Quit"])
                self.ui.choice_buttons[0].config(command=self.ui.ask_new_game)
                self.ui.choice_buttons[1].config(command=self.root.quit)
            else:
                self.ui.update_choices(self.last_options)
            return True
        except Exception as e:
            self.ui.show_error(str(e))
            return False

    def use_item(self, item):
        if item not in self.game_state.inventory:
            return
        self.ui.end_reading_mode(show_options=False)
        self.game_state.inventory.remove(item)
        self.ui.update_stats(self.game_state.stats, self.game_state.inventory)
        action = f"Use item: {item}"
        self.game_state.log_story("User", action)
        self.ui.update_display(action, "User")
        self.ui.disable_choices()
        threading.Thread(target=self._ai_next_turn, args=(action,), daemon=True).start()

def main():
    root = tk.Tk()
    app = StoryDirectorApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
