class Utility:
    def __init__(self):
        pass

    @staticmethod
    # Title: say_hello
    # Description: A quick check that this file is importable and working
    # Input: name:String - optional, default: "world"
    # Output: String - a message
    def say_hello(name: str = "world") -> str:
        return f"Hello, {name}!"


    # ---------------------------------------------------------------------------
    # How to use this in a notebook:
    #
    # import os
    # print(os.getcwd())
    # if your output is not "/workspace/notebooks" then run os.chdir("/workspace/notebooks")
    # from Utility import Utility as Helper
    # print(Helper.say_hello())
    # print(say_hello("Kartik"))  # Hello, Kartik!
    #
    # Make sure notebooks.py is either in the same folder as your notebook,
    # or on your Python path, or this import will fail.
    # ---------------------------------------------------------------------------