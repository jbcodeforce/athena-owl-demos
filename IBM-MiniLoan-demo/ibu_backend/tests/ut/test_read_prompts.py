import yaml,sys,os
import unittest
module_path = "./src"
sys.path.append(os.path.abspath(module_path))
os.environ["CONFIG_FILE"] = "./tests/ut/config/config.yaml"

from athena.llm.prompts.prompt_mgr import get_prompt_manager, OwlPromptEntity

class TestPrompts(unittest.TestCase):
    def test_build_prompt_from_prompt_entity(self):
        print("\n\n --- test_build_prompt_instance_from_prompt_entity")
        p_mgr=get_prompt_manager()
        prompt = p_mgr.build_prompt("ibu_loan_prompt","en")
        print(type(prompt))
        print(prompt) 



if __name__ == '__main__':
    unittest.main()