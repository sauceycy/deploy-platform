import tempfile
import unittest
from pathlib import Path

import server


class JavaRuntimeTests(unittest.TestCase):
    def test_generated_dockerfile_has_optional_vm_and_app_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            src_dir = Path(directory)
            jar = src_dir / "target" / "demo.jar"
            jar.parent.mkdir()
            jar.write_bytes(b"jar")
            task = {"language": "java", "sdk": "jdk17", "jvmOptions": "-Xms256m -Xmx512m"}

            dockerfile, context, artifact = server.generate_dockerfile(task, src_dir, src_dir)
            content = dockerfile.read_text(encoding="utf-8")

            self.assertEqual(artifact, "target/demo.jar")
            self.assertEqual((context / "app.jar").read_bytes(), b"jar")
            self.assertIn('ENV VM_OPTS="-Xms256m -Xmx512m"', content)
            self.assertIn('ENV APP_OPTS=""', content)
            self.assertIn('ENTRYPOINT ["/bin/sh","-c","exec java ${VM_OPTS:-} -jar /app/app.jar ${APP_OPTS:-}"]', content)

            task["jvmOptions"] = ""
            dockerfile, _, _ = server.generate_dockerfile(task, src_dir, src_dir)
            self.assertIn('ENV VM_OPTS=""', dockerfile.read_text(encoding="utf-8"))

    def test_manifest_injects_vm_and_app_options(self):
        task = {
            "name": "demo", "language": "java", "jvmOptions": "-Xmx512m",
            "runtimeEnv": "APP_OPTS=--spring.profiles.active=test",
        }
        manifest = server.create_manifest(task, {"namespace": "default"}, "example/demo:1")

        self.assertIn('name: VM_OPTS\n              value: "-Xmx512m"', manifest)
        self.assertIn('name: APP_OPTS\n              value: "--spring.profiles.active=test"', manifest)
        self.assertNotIn("JAVA_TOOL_OPTIONS", manifest)


if __name__ == "__main__":
    unittest.main()
