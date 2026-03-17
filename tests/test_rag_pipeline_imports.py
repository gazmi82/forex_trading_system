from __future__ import annotations

import unittest

from app.rag import (
    DocumentProcessor as PackagedDocumentProcessor,
    RAGPipeline as PackagedRAGPipeline,
    TextChunker as PackagedTextChunker,
    VectorStore as PackagedVectorStore,
)
from app.rag.pipeline import DocumentProcessor, RAGPipeline, TextChunker, VectorStore
from rag_pipeline import (
    DocumentProcessor as RootDocumentProcessor,
    RAGPipeline as RootRAGPipeline,
    TextChunker as RootTextChunker,
    VectorStore as RootVectorStore,
)


class RAGPipelineImportTests(unittest.TestCase):
    def test_root_rag_pipeline_reexports_packaged_types(self):
        self.assertIs(RootDocumentProcessor, DocumentProcessor)
        self.assertIs(RootTextChunker, TextChunker)
        self.assertIs(RootVectorStore, VectorStore)
        self.assertIs(RootRAGPipeline, RAGPipeline)
        self.assertIs(PackagedDocumentProcessor, DocumentProcessor)
        self.assertIs(PackagedTextChunker, TextChunker)
        self.assertIs(PackagedVectorStore, VectorStore)
        self.assertIs(PackagedRAGPipeline, RAGPipeline)


if __name__ == "__main__":
    unittest.main()
