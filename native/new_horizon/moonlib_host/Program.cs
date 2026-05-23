using moonlib;

namespace moonlib_host;

internal static class Program
{
    private static int Main(string[] args)
    {
        var value = BridgeSmoke.AddOne(1.0f);
        Console.WriteLine($"moonlib_host bridge={value:F1}");
        return 0;
    }
}
