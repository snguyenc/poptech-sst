import threading
import time
import requests
import logging
import asyncio

log = logging.getLogger(__name__)

class AutoSummary:
    """Manages automatic, periodic summarization of transcripts."""

    def __init__(self, n8n_webhook_url: str, interval: int = 60, min_summary_words: int = 30, summary_callback=None, loop=None):
        """
        Initializes the AutoSummary instance.

        Args:
            n8n_webhook_url (str): The URL of the n8n webhook to call.
            interval (int): The interval in seconds for sending summaries.
        """
        self.n8n_webhook_url = n8n_webhook_url
        self.interval = interval
        self.min_summary_words = min_summary_words
        self.transcript_parts = []
        self.lock = threading.Lock()
        self.timer_thread = None
        self.stop_event = threading.Event()
        self.is_running = False
        self.summary_callback = summary_callback
        self.prevSummaryText = ""
        self.loop = loop

    def add_transcript(self, text: str):
        """Adds a piece of transcript to the buffer."""
        if not text:
            return
        with self.lock:
            self.transcript_parts.append(text)
            log.debug(f"Added transcript part. Total parts: {len(self.transcript_parts)}")

    def _get_full_transcript(self) -> str:
        """Joins all transcript parts into a single string."""
        with self.lock:
            return " ".join(self.transcript_parts)

    def _clear_transcript(self):
        """Clears the transcript buffer."""
        with self.lock:
            self.prevSummaryText = ""
            self.transcript_parts.clear()

    def _clear_transcript_parts(self):
        """Clears only the transcript parts buffer, for interim summaries."""
        with self.lock:
            self.transcript_parts.clear()
            log.info("Cleared interim transcript parts buffer.")

    def send_summary(self, is_final: bool = False):
        """Sends the accumulated transcript to the n8n webhook."""
        transcript_to_send = self._get_full_transcript()
        if not transcript_to_send.strip():
            log.info("No new transcript to summarize. Skipping.")
            return

        if len(transcript_to_send.split()) < self.min_summary_words:
            log.info("Transcript is too short to summarize. Skipping.")
            return
            
        log.info(f"Sending {'final' if is_final else 'interim'} summary with {len(transcript_to_send.split())} words...")
        try:
            payload = {
                "text": transcript_to_send,
                "prevSummaryText": self.prevSummaryText,
                "is_final": is_final
            }
            response = requests.post(self.n8n_webhook_url, json=payload, timeout=15)
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            json_response = response.json()
            summary_text = json_response.get('text', '')
            self.prevSummaryText = summary_text            
            log.info(f"Successfully sent summary to n8n. Status: {response.status_code} {summary_text}")

            if self.summary_callback and self.loop and summary_text:
                message = {
                    "action": "summary_update",
                    "text": summary_text
                }
                # Schedule the async callback to be run in the main event loop
                asyncio.run_coroutine_threadsafe(self.summary_callback(message), self.loop)

            # For interim summaries, clear the buffer after sending
            if not is_final:
                self._clear_transcript_parts()
        except requests.exceptions.RequestException as e:
            log.error(f"Failed to send summary to n8n: {e}")

    def _timer_loop(self):
        """The main loop for the timer thread."""
        while not self.stop_event.wait(self.interval):
            if self.is_running:
                log.info(f"Auto-summary timer triggered after {self.interval}s.")
                self.send_summary(is_final=False)

    def start(self):
        """Starts the automatic summarization process."""
        if self.is_running:
            log.warning("AutoSummary is already running.")
            return

        log.info("Starting AutoSummary service...")
        self.stop_event.clear()
        self.is_running = True
        self.prevSummaryText = ""
        self.timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self.timer_thread.start()
        log.info("AutoSummary service started.")

    def stop(self):
        """Stops the automatic summarization and sends a final summary."""
        if not self.is_running:
            log.warning("AutoSummary is not running.")
            return

        log.info("Stopping AutoSummary service...")
        self.is_running = False
        self.stop_event.set() # Signal the timer loop to exit

        if self.timer_thread and self.timer_thread.is_alive():
            self.timer_thread.join(timeout=2.0) # Wait for the thread to finish

        # Send the final summary
        log.info("Sending final summary before shutdown.")
        self.send_summary(is_final=True)
        self._clear_transcript()
        log.info("AutoSummary service stopped.")
