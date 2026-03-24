from __future__ import annotations

import unittest

from app.rag import (
    DocumentProcessor as PackagedDocumentProcessor,
    RAGPipeline as PackagedRAGPipeline,
    TextChunker as PackagedTextChunker,
    VectorStore as PackagedVectorStore,
)
from app.rag.pipeline import DocumentProcessor, RAGPipeline, TextChunker, VectorStore


class RAGPipelineImportTests(unittest.TestCase):
    def test_app_rag_init_reexports_pipeline_types(self):
        self.assertIs(PackagedDocumentProcessor, DocumentProcessor)
        self.assertIs(PackagedTextChunker, TextChunker)
        self.assertIs(PackagedVectorStore, VectorStore)
        self.assertIs(PackagedRAGPipeline, RAGPipeline)


if __name__ == "__main__":
    unittest.main()
