"""The reason texts the watch, the chime and the voice show (Feature 4, 7 and 9). The same on every system.

The system-specific pieces report a short code; this is where each code becomes its words.
"""

# Password store codes (the secret store piece answers with these).
NO_STORE, LOCKED, FAILED = "no-store", "locked", "failed"
STORE_REASONS = {
    NO_STORE: "no password store is running on this desktop.",
    LOCKED: "the password store is locked or access was refused.",
    FAILED: "the password store reported an error.",
}
STORE_UNAVAILABLE = "keeping the key is not available on this system yet."

# Sound player codes (the sound player piece answers with these).
NO_DEVICE, SOUND_UNAVAILABLE, SOUND_FAILED = "no-device", "unavailable", "failed"
SOUND_REASONS = {
    NO_DEVICE: "no sound device was found.",
    SOUND_UNAVAILABLE: "sound is not available on this system yet.",
    SOUND_FAILED: "the sound system could not play it.",
}

WATCH_NOT_AVAILABLE = "Not available on this system yet."
HELPER_NO_START = "The watch helper could not start."
HELPER_TWICE = "The watch helper stopped twice within a minute."
WATCH_FAILED = "The game watch could not open."
NO_GAME_FOLDER = "Set your game folder in the General tab."
NO_CHAT_LOG = "No game chat log found. Turn on chat logging in the game's options."
CHIME_FAILED = "Alert chime could not play:"
SPEAK_FAILED = "Last alert could not be spoken:"
KEY_PROMPT = "Enter your Fish Audio key to load voices."
KEY_LOADING = "Loading voices..."
VOICES_FAILED = "Could not load voices:"
KEY_READ_FAILED = "Could not read your Fish Audio key:"
KEY_SAVE_FAILED = "Could not save your Fish Audio key:"


def capital(text):
    return text[:1].upper() + text[1:]
