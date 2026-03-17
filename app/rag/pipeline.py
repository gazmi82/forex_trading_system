# =============================================================================
# rag_pipeline.py — Full RAG Pipeline
# Handles: PDF ingestion → chunking → embedding → ChromaDB storage → retrieval
#
# FREE tools used:
#   - sentence-transformers  (local embeddings, no API cost)
#   - ChromaDB               (local vector database, no API cost)
#   - pypdf2                 (PDF parsing)
#
# Install: pip install chromadb sentence-transformers pypdf2 python-docx
# =============================================================================

import os
import re
import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# LAZY IMPORTS — Only load heavy libraries when actually needed
# =============================================================================

def _get_chromadb():
    try:
        import chromadb
        return chromadb
    except ImportError:
        raise ImportError("Run: pip install chromadb")

def _get_embedder():
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer
    except ImportError:
        raise ImportError("Run: pip install sentence-transformers")

def _get_pypdf():
    try:
        import pypdf
        return pypdf
    except ImportError:
        try:
            import PyPDF2 as pypdf
            return pypdf
        except ImportError:
            raise ImportError("Run: pip install pypdf")


# =============================================================================
# DOCUMENT PROCESSOR — Handles all file types
# =============================================================================

class DocumentProcessor:
    """Extracts clean text from PDF, TXT, and MD files."""

    @staticmethod
    def extract_text(file_path: Path) -> Optional[str]:
        suffix = file_path.suffix.lower()

        if suffix == ".pdf":
            return DocumentProcessor._extract_pdf(file_path)
        elif suffix in [".txt", ".md", ".text"]:
            return DocumentProcessor._extract_text_file(file_path)
        elif suffix == ".docx":
            return DocumentProcessor._extract_docx(file_path)
        else:
            logger.warning(f"Unsupported file type: {suffix} — {file_path.name}")
            return None

    @staticmethod
    def _extract_pdf(file_path: Path) -> str:
        pypdf = _get_pypdf()
        text_parts = []

        try:
            with open(file_path, "rb") as f:
                # Try modern pypdf first
                try:
                    reader = pypdf.PdfReader(f)
                    for page in reader.pages:
                        text = page.extract_text()
                        if text:
                            text_parts.append(text)
                except AttributeError:
                    # Fallback to PyPDF2 style
                    reader = pypdf.PdfFileReader(f)
                    for i in range(reader.numPages):
                        text = reader.getPage(i).extractText()
                        if text:
                            text_parts.append(text)

            full_text = "\n".join(text_parts)
            return DocumentProcessor._clean_text(full_text)

        except Exception as e:
            logger.error(f"PDF extraction failed for {file_path.name}: {e}")
            return ""

    @staticmethod
    def _extract_text_file(file_path: Path) -> str:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return DocumentProcessor._clean_text(f.read())
        except Exception as e:
            logger.error(f"Text extraction failed for {file_path.name}: {e}")
            return ""

    @staticmethod
    def _extract_docx(file_path: Path) -> str:
        try:
            import docx
            doc = docx.Document(file_path)
            text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
            return DocumentProcessor._clean_text(text)
        except ImportError:
            logger.warning("python-docx not installed. Run: pip install python-docx")
            return ""
        except Exception as e:
            logger.error(f"DOCX extraction failed for {file_path.name}: {e}")
            return ""

    @staticmethod
    def _clean_text(text: str) -> str:
        """Remove noise while preserving meaningful content."""
        # Remove excessive whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {3,}', ' ', text)
        # Remove page numbers (standalone numbers)
        text = re.sub(r'\n\s*\d+\s*\n', '\n', text)
        # Remove common PDF artifacts
        text = re.sub(r'[^\x00-\x7F]+', ' ', text)
        return text.strip()


# =============================================================================
# CHUNKER — Splits text into overlapping semantic chunks
# =============================================================================

class TextChunker:
    """Splits documents into overlapping chunks for better retrieval."""

    def __init__(self, chunk_size: int = 600, overlap: int = 100):
        self.chunk_size = chunk_size    # Words per chunk
        self.overlap = overlap          # Word overlap between chunks

    def chunk(self, text: str, source_name: str) -> list[dict]:
        """Returns list of chunk dicts with text and metadata."""
        words = text.split()

        if len(words) < 50:
            logger.warning(f"Document too short to chunk: {source_name} ({len(words)} words)")
            return []

        chunks = []
        step = self.chunk_size - self.overlap
        chunk_index = 0

        for start in range(0, len(words), step):
            end = min(start + self.chunk_size, len(words))
            chunk_words = words[start:end]
            chunk_text = " ".join(chunk_words)

            # Skip very short final chunks
            if len(chunk_words) < 50:
                break

            chunks.append({
                "text":         chunk_text,
                "source":       source_name,
                "chunk_index":  chunk_index,
                "word_count":   len(chunk_words),
                "char_count":   len(chunk_text),
            })
            chunk_index += 1

        logger.info(f"  Chunked '{source_name}' → {len(chunks)} chunks")
        return chunks


# =============================================================================
# VECTOR STORE — ChromaDB wrapper for storing and searching embeddings
# =============================================================================

class VectorStore:
    """
    Local ChromaDB vector store. Completely free, runs on your machine.
    Stores document chunks as numerical embeddings for semantic search.
    """

    def __init__(self, client, collection_name: str, embedder):
        self.collection_name = collection_name
        self.client = client
        self.embedder = embedder

        # Initialize persistent ChromaDB client
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}   # Cosine similarity
        )

        logger.info(f"VectorStore ready: {collection_name} ({self.collection.count()} docs)")

    def add_chunks(self, chunks: list[dict], category: str, source_file: str) -> int:
        """Add document chunks to the vector store. Skips duplicates."""
        if not chunks:
            return 0

        added = 0
        for chunk in chunks:
            # Create unique ID based on content hash (prevents duplicates)
            content_hash = hashlib.md5(chunk["text"].encode()).hexdigest()
            doc_id = f"{category}_{content_hash}"

            # Check if already exists
            existing = self.collection.get(ids=[doc_id])
            if existing["ids"]:
                continue  # Skip duplicate

            # Generate embedding locally (no API cost)
            embedding = self.embedder.encode(chunk["text"]).tolist()

            # Store in ChromaDB
            self.collection.add(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[chunk["text"]],
                metadatas=[{
                    "source":       chunk["source"],
                    "category":     category,
                    "source_file":  source_file,
                    "chunk_index":  chunk["chunk_index"],
                    "word_count":   chunk["word_count"],
                    "added_at":     datetime.utcnow().isoformat(),
                }]
            )
            added += 1

        logger.info(f"  Added {added} new chunks to '{self.collection_name}'")
        return added

    def search(self, query: str, top_k: int = 5, category_filter: str = None) -> list[dict]:
        """Semantic search — finds most relevant chunks for a query."""
        if self.collection.count() == 0:
            return []

        # Generate query embedding
        query_embedding = self.embedder.encode(query).tolist()

        # Build filter
        where = {"category": category_filter} if category_filter else None

        # Search
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, self.collection.count()),
            where=where,
            include=["documents", "metadatas", "distances"]
        )

        # Format results
        chunks = []
        for i, doc in enumerate(results["documents"][0]):
            distance = results["distances"][0][i]
            similarity = 1 - distance   # Convert distance to similarity

            chunks.append({
                "text":         doc,
                "source":       results["metadatas"][0][i]["source"],
                "category":     results["metadatas"][0][i]["category"],
                "source_file":  results["metadatas"][0][i]["source_file"],
                "similarity":   round(similarity, 3),
                "chunk_index":  results["metadatas"][0][i]["chunk_index"],
            })

        return chunks

    def count(self) -> int:
        return self.collection.count()


# =============================================================================
# RAG PIPELINE — Main orchestrator
# =============================================================================

class RAGPipeline:
    """
    Full RAG Pipeline:
    1. Ingests documents (PDF, TXT, MD) from directory
    2. Chunks and embeds them
    3. Stores in ChromaDB (local, free)
    4. Provides semantic search at runtime
    5. Formats retrieved context for Claude injection
    """

    def __init__(self, config: dict, chroma_dir: str):
        chromadb = _get_chromadb()
        SentenceTransformer = _get_embedder()

        self.config = config
        self.chroma_dir = chroma_dir
        self.processor = DocumentProcessor()
        self.chunker = TextChunker(
            chunk_size=config["chunk_size"],
            overlap=config["chunk_overlap"]
        )

        # Load one embedding model and one Chroma client per process.
        # Reusing them across collections avoids repeated model downloads/checks.
        embedding_model = config["embedding_model"]
        print(f"\n📚 Initializing RAG Pipeline...")
        print(f"  Loading embedding model: {embedding_model}")
        self.embedder = SentenceTransformer(embedding_model)
        self.client = chromadb.PersistentClient(path=chroma_dir)

        # One vector store per knowledge category
        self.stores: dict[str, VectorStore] = {}
        for category, collection_name in config["collections"].items():
            self.stores[category] = VectorStore(
                collection_name=collection_name,
                client=self.client,
                embedder=self.embedder,
            )

        print(f"✅ RAG Pipeline ready with {len(self.stores)} knowledge categories\n")

    # -------------------------------------------------------------------------
    # INGESTION
    # -------------------------------------------------------------------------

    def ingest_directory(self, directory: Path, category: str) -> dict:
        """
        Ingests all supported files from a directory into the vector store.
        Safe to re-run — skips already-processed documents.

        Usage:
            pipeline.ingest_directory(Path("documents/books"), "books")
            pipeline.ingest_directory(Path("documents/ict"), "ict")
        """
        if not directory.exists():
            logger.warning(f"Directory does not exist: {directory}")
            return {"processed": 0, "skipped": 0, "errors": 0}

        supported = [".pdf", ".txt", ".md", ".docx"]
        files = [f for f in directory.iterdir() if f.suffix.lower() in supported]
        files = self._prefer_clean_text(files)

        if not files:
            print(f"  ℹ️  No documents found in {directory}")
            return {"processed": 0, "skipped": 0, "errors": 0}

        print(f"\n📂 Ingesting {len(files)} files from '{category}'...")
        results = {"processed": 0, "skipped": 0, "errors": 0, "total_chunks": 0}

        for file_path in files:
            try:
                print(f"  Processing: {file_path.name}")

                # Extract text
                text = self.processor.extract_text(file_path)
                if not text or len(text) < 200:
                    print(f"  ⚠️  Skipped (too short or empty): {file_path.name}")
                    results["skipped"] += 1
                    continue

                # Chunk text
                chunks = self.chunker.chunk(text, source_name=file_path.stem)
                if not chunks:
                    results["skipped"] += 1
                    continue

                # Store in vector store
                added = self.stores[category].add_chunks(
                    chunks=chunks,
                    category=category,
                    source_file=file_path.name
                )

                results["processed"] += 1
                results["total_chunks"] += added
                print(f"  ✅ {file_path.name} → {added} chunks added")

            except Exception as e:
                logger.error(f"Error processing {file_path.name}: {e}")
                results["errors"] += 1

        print(f"\n  Summary: {results['processed']} processed, "
              f"{results['total_chunks']} chunks added, "
              f"{results['errors']} errors\n")
        return results

    def _prefer_clean_text(self, files: list[Path]) -> list[Path]:
        """
        Prefer cleaned text artifacts over same-stem PDFs to avoid duplicate
        indexing after PDF→Markdown conversion.
        """
        priority = {
            ".md": 0,
            ".txt": 1,
            ".text": 1,
            ".docx": 2,
            ".pdf": 3,
        }
        preferred: dict[str, Path] = {}

        for file_path in sorted(files):
            key = file_path.stem.lower()
            current = preferred.get(key)
            if current is None:
                preferred[key] = file_path
                continue

            current_rank = priority.get(current.suffix.lower(), 99)
            candidate_rank = priority.get(file_path.suffix.lower(), 99)
            if candidate_rank < current_rank:
                preferred[key] = file_path

        return sorted(preferred.values())

    def ingest_text(self, text: str, source_name: str, category: str) -> int:
        """
        Ingest raw text directly (for ICT transcripts, journal entries, etc.)

        Usage:
            pipeline.ingest_text(ict_transcript, "ICT_2024_mentorship", "ict")
            pipeline.ingest_text(journal_entry, "journal_2025_01_15", "journal")
        """
        chunks = self.chunker.chunk(text, source_name=source_name)
        if not chunks:
            return 0

        added = self.stores[category].add_chunks(
            chunks=chunks,
            category=category,
            source_file=f"{source_name}.txt"
        )
        return added

    def ingest_all_documents(self, documents_dir: Path):
        """
        Ingests everything from all subdirectories at once.
        Run this on startup to ensure all documents are loaded.
        """
        print("\n" + "="*60)
        print("📚 INGESTING ALL DOCUMENTS")
        print("="*60)

        category_dirs = {
            "books":    documents_dir / "books",
            "research": documents_dir / "research",
            "ict":      documents_dir / "ict",
            "cot":      documents_dir / "cot",
            "journal":  documents_dir / "journal",
        }

        total_chunks = 0
        for category, dir_path in category_dirs.items():
            if dir_path.exists():
                result = self.ingest_directory(dir_path, category)
                total_chunks += result.get("total_chunks", 0)

        print(f"\n✅ Total knowledge base: {self.get_stats()['total_documents']} chunks\n")

    # -------------------------------------------------------------------------
    # RETRIEVAL — The core of RAG
    # -------------------------------------------------------------------------

    def search(
        self,
        query: str,
        categories: list[str] = None,
        top_k: int = None
    ) -> list[dict]:
        """
        Semantic search across one or more knowledge categories.

        Args:
            query:      Natural language query
            categories: Which stores to search (None = search all)
            top_k:      Results per category

        Returns:
            List of relevant chunks sorted by similarity
        """
        if top_k is None:
            top_k = self.config["top_k_results"]

        if categories is None:
            categories = list(self.stores.keys())

        all_results = []
        for category in categories:
            if category in self.stores and self.stores[category].count() > 0:
                results = self.stores[category].search(query, top_k=top_k)
                all_results.extend(results)

        # Sort by similarity, return top results
        all_results.sort(key=lambda x: x["similarity"], reverse=True)

        # Filter by similarity threshold
        threshold = self.config["similarity_threshold"]
        filtered = [r for r in all_results if r["similarity"] >= threshold]

        return filtered[:top_k * 2]   # Return up to 2x top_k across all categories

    def search_for_trading_context(self, market_state: dict) -> dict:
        """
        Runs targeted searches based on current market conditions.
        This is the main function called before each analysis.

        Returns structured context ready for injection into Claude prompt.
        """
        queries = self._build_contextual_queries(market_state)
        all_chunks = {}

        for query_name, query_text in queries.items():
            results = self.search(
                query=query_text,
                categories=self._get_relevant_categories(query_name),
                top_k=3
            )
            if results:
                all_chunks[query_name] = results

        return all_chunks

    def _build_contextual_queries(self, market_state: dict) -> dict:
        """Builds targeted queries based on what is happening in the market."""
        pair = market_state.get("pair", "EUR/USD")
        trend = market_state.get("trend", "neutral")
        regime = market_state.get("regime", "unknown")
        next_event = market_state.get("next_event", "")
        session = market_state.get("session", "")

        queries = {}

        # Always search for pair-specific knowledge
        queries["pair_knowledge"] = (
            f"trading {pair} forex strategy best practices entry exit"
        )

        # Trend-specific knowledge
        if trend in ["bullish", "bearish"]:
            queries["trend_strategy"] = (
                f"forex trend following strategy {trend} market "
                f"order blocks fair value gaps ICT"
            )
        else:
            queries["range_strategy"] = (
                "forex ranging market mean reversion strategy "
                "support resistance bounce"
            )

        # Regime-specific
        if regime == "high_volatility":
            queries["volatility_management"] = (
                "high volatility forex risk management position sizing "
                "stop loss placement volatile market"
            )

        # News event preparation
        if next_event and "NFP" in next_event.upper():
            queries["news_event"] = (
                "NFP non-farm payrolls forex trading strategy "
                "how to trade NFP news release"
            )
        elif next_event and "FOMC" in next_event.upper():
            queries["news_event"] = (
                "FOMC federal reserve forex impact trading strategy "
                "interest rate decision currency"
            )
        elif next_event and "CPI" in next_event.upper():
            queries["news_event"] = (
                "CPI inflation data forex trading impact "
                "EUR USD reaction to inflation"
            )

        # Session-specific knowledge
        if "london" in session.lower():
            queries["session_knowledge"] = (
                "London session forex trading kill zone institutional "
                "order flow London open strategy"
            )
        elif "new york" in session.lower() or "ny" in session.lower():
            queries["session_knowledge"] = (
                "New York session forex trading kill zone NY open "
                "reversal strategy institutional"
            )

        # Always get risk management knowledge
        queries["risk_management"] = (
            "forex risk management position sizing stop loss "
            "risk reward ratio professional trading"
        )

        # Recent feedback (from agent's own trade history)
        queries["agent_feedback"] = (
            f"trade review {pair} what went wrong correction lesson"
        )

        return queries

    def _get_relevant_categories(self, query_name: str) -> list[str]:
        """Maps query type to most relevant knowledge categories."""
        category_map = {
            "pair_knowledge":       ["books", "research", "ict"],
            "trend_strategy":       ["ict", "books"],
            "range_strategy":       ["ict", "books"],
            "volatility_management":["books", "research"],
            "news_event":           ["books", "research", "cot"],
            "session_knowledge":    ["ict", "books"],
            "risk_management":      ["books", "research"],
            "agent_feedback":       ["feedback", "journal"],
            "cot_analysis":         ["cot", "research"],
        }
        return category_map.get(query_name, ["books", "ict", "research"])

    # -------------------------------------------------------------------------
    # CONTEXT FORMATTER — Prepares RAG results for Claude injection
    # -------------------------------------------------------------------------

    def format_rag_context(
        self,
        retrieved_chunks: dict,
        max_tokens: int = 3000
    ) -> str:
        """
        Formats retrieved chunks into a clean context block
        ready to be injected into Claude's prompt.

        Estimates token count and truncates if needed.
        """
        if not retrieved_chunks:
            return ""

        sections = []
        estimated_tokens = 0
        chars_per_token = 4     # Rough estimate

        sections.append(
            "═══════════════════════════════════════════\n"
            "KNOWLEDGE BASE — RELEVANT EXPERT KNOWLEDGE\n"
            "Retrieved from your trading library for this analysis\n"
            "═══════════════════════════════════════════\n"
        )

        for query_name, chunks in retrieved_chunks.items():
            if not chunks:
                continue

            # Format query category name
            category_label = query_name.replace("_", " ").title()
            section_header = f"\n📚 {category_label}:\n"

            section_chunks = []
            for chunk in chunks[:2]:    # Max 2 chunks per query
                source_label = chunk["source"].replace("_", " ").title()
                similarity_pct = int(chunk["similarity"] * 100)
                chunk_text = (
                    f"[Source: {source_label} | "
                    f"Relevance: {similarity_pct}%]\n"
                    f"{chunk['text']}\n"
                )
                section_chunks.append(chunk_text)

            section_text = section_header + "\n".join(section_chunks)
            section_tokens = len(section_text) // chars_per_token

            if estimated_tokens + section_tokens > max_tokens:
                sections.append(
                    f"\n[Knowledge base truncated to stay within context limit]\n"
                )
                break

            sections.append(section_text)
            estimated_tokens += section_tokens

        sections.append(
            "\n═══════════════════════════════════════════\n"
            "Use the above knowledge to inform your analysis.\n"
            "Cite specific sources in your reasoning where relevant.\n"
            "═══════════════════════════════════════════\n"
        )

        return "\n".join(sections)

    # -------------------------------------------------------------------------
    # FEEDBACK MEMORY — Stores and retrieves agent trade reviews
    # -------------------------------------------------------------------------

    def store_feedback(self, feedback_text: str, trade_date: str, pair: str):
        """
        Stores post-trade feedback in the feedback collection.
        This creates a rolling memory of the agent's own learning.
        """
        source_name = f"feedback_{pair}_{trade_date}"
        added = self.ingest_text(feedback_text, source_name, "feedback")
        logger.info(f"Stored feedback: {source_name} ({added} chunks)")
        return added

    def get_recent_feedback(self, pair: str, top_k: int = 5) -> list[dict]:
        """Retrieves recent feedback relevant to the current pair."""
        query = f"trade review analysis {pair} lesson learned improvement"
        return self.stores["feedback"].search(query, top_k=top_k)

    # -------------------------------------------------------------------------
    # STATS & MAINTENANCE
    # -------------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Returns knowledge base statistics."""
        stats = {"categories": {}, "total_documents": 0}
        for category, store in self.stores.items():
            count = store.count()
            stats["categories"][category] = count
            stats["total_documents"] += count
        return stats

    def print_stats(self):
        """Prints a formatted knowledge base summary."""
        stats = self.get_stats()
        print("\n" + "="*50)
        print("📊 KNOWLEDGE BASE STATISTICS")
        print("="*50)
        for category, count in stats["categories"].items():
            status = "✅" if count > 0 else "⚠️  empty"
            print(f"  {status} {category:15} {count:>6} chunks")
        print(f"\n  {'TOTAL':15} {stats['total_documents']:>6} chunks")
        print("="*50 + "\n")


# =============================================================================
# STANDALONE USAGE EXAMPLE
# =============================================================================

if __name__ == "__main__":
    from app.core.config import CHROMA_DIR, DOCUMENTS_DIR, RAG_CONFIG

    # Initialize pipeline
    pipeline = RAGPipeline(
        config=RAG_CONFIG,
        chroma_dir=str(CHROMA_DIR)
    )

    # Ingest all documents
    pipeline.ingest_all_documents(DOCUMENTS_DIR)

    # Print stats
    pipeline.print_stats()

    # Example search
    results = pipeline.search(
        "How should I trade EUR/USD during London Kill Zone with Order Blocks?",
        categories=["ict", "books"]
    )

    print("\n🔍 EXAMPLE SEARCH RESULTS:")
    for r in results[:2]:
        print(f"\nSource: {r['source']} (Relevance: {r['similarity']*100:.0f}%)")
        print(f"Excerpt: {r['text'][:200]}...")
