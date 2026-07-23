from __future__ import annotations

import unittest

import kb_bridge


class BridgeTests(unittest.TestCase):
    def test_scope_separates_core_and_experience_databases(self) -> None:
        config = kb_bridge.load_config(kb_bridge.DEFAULT_CONFIG)
        core = kb_bridge.config_for_scope(config, "core")
        experience = kb_bridge.config_for_scope(config, "experience")
        multimodal = kb_bridge.config_for_scope(config, "multimodal")
        all_text = kb_bridge.config_for_scope(config, "all")
        core_keys = {item["key"] for item in core["knowledge_bases"] if item.get("enabled", True)}
        experience_keys = {
            item["key"] for item in experience["knowledge_bases"] if item.get("enabled", True)
        }
        multimodal_keys = {
            item["key"] for item in multimodal["knowledge_bases"] if item.get("enabled", True)
        }
        all_text_keys = {
            item["key"] for item in all_text["knowledge_bases"] if item.get("enabled", True)
        }
        self.assertEqual(core_keys, kb_bridge.KNOWLEDGE_SCOPE_KEYS["core"])
        self.assertEqual(experience_keys, kb_bridge.KNOWLEDGE_SCOPE_KEYS["experience"])
        self.assertEqual(multimodal_keys, {"multimodal_cases"})
        self.assertTrue(core_keys.isdisjoint(experience_keys))
        self.assertTrue(all_text_keys.isdisjoint(multimodal_keys))
        self.assertEqual(all_text_keys, core_keys | experience_keys)

    def test_resolve_all_databases_by_name(self) -> None:
        config = kb_bridge.load_config(kb_bridge.DEFAULT_CONFIG)
        remote = [
            {"id": f"dataset-{index}", "name": item["dify_name"]}
            for index, item in enumerate(config["knowledge_bases"], start=1)
        ]
        resolved, errors = kb_bridge.resolve_databases(config, remote)
        self.assertEqual(errors, [])
        enabled_count = sum(item.get("enabled", True) for item in config["knowledge_bases"])
        self.assertEqual(len(resolved), enabled_count)
        self.assertEqual(sum(item.role == "multimodal" for item in resolved), 1)

    def test_build_result_separates_text_and_images(self) -> None:
        text_db = kb_bridge.Database("text", "文本库", "d1", "text", "", {})
        image_db = kb_bridge.Database("mm", "多模态库", "d2", "multimodal", "", {})
        outcomes = [
            {
                "database": text_db,
                "elapsed_ms": 10,
                "records": [
                    {
                        "score": 0.9,
                        "segment": {
                            "id": "s1",
                            "content": "模型正文",
                            "document": {"id": "doc1", "name": "案例.md"},
                        },
                        "files": [],
                    }
                ],
            },
            {
                "database": image_db,
                "elapsed_ms": 20,
                "records": [
                    {
                        "score": 0.8,
                        "segment": {
                            "id": "s2",
                            "content": "图 2 炉温曲线",
                            "document": {"id": "doc2", "name": "案例图.md"},
                        },
                        "files": [
                            {
                                "id": "file1",
                                "name": "figure.jpg",
                                "source_url": "/files/file1/file-preview?sign=x",
                            }
                        ],
                    }
                ],
            },
        ]
        config = {
            "merge": {
                "max_text_hits": 10,
                "max_multimodal_context_hits": 10,
                "max_image_hits": 10,
            }
        }
        result = kb_bridge.build_result(
            "炉温曲线", outcomes, [], config, "https://dify.example.com/v1", 30
        )
        self.assertEqual(len(result["text_hits"]), 1)
        self.assertEqual(len(result["multimodal_context_hits"]), 1)
        self.assertEqual(len(result["image_hits"]), 1)
        self.assertEqual(
            result["image_hits"][0]["source_url"],
            "https://dify.example.com/files/file1/file-preview?sign=x",
        )
        self.assertEqual(result["multimodal_context_hits"][0]["matched_child_chunks"], [])


if __name__ == "__main__":
    unittest.main()
