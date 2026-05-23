using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace moonlib.tests
{
    [TestClass]
    public class HorizonProgressContractTests
    {
        [TestMethod]
        public void HorizonProgressRecordCarriesStructuredProgressFields()
        {
            var progress = new HorizonProgress(
                ProcessedPatches: 3,
                TotalPatches: 10,
                Percent: 30.0,
                Stage: "process_patches",
                Message: "Generated 3/10 horizon patches.",
                FileName: "horizon_00000_00000_000.cbin");

            Assert.AreEqual(3, progress.ProcessedPatches);
            Assert.AreEqual(10, progress.TotalPatches);
            Assert.AreEqual(30.0, progress.Percent);
            Assert.AreEqual("process_patches", progress.Stage);
            Assert.AreEqual("Generated 3/10 horizon patches.", progress.Message);
            Assert.AreEqual("horizon_00000_00000_000.cbin", progress.FileName);
        }

        [TestMethod]
        public void HorizonProgressCallbackCanBeInvoked()
        {
            HorizonProgress? captured = null;
            HorizonProgressCallback callback = progress => captured = progress;

            callback(new HorizonProgress(
                ProcessedPatches: 1,
                TotalPatches: 2,
                Percent: 50.0,
                Stage: "process_patches",
                Message: "Generated 1/2 horizon patches.",
                FileName: null));

            Assert.IsNotNull(captured);
            Assert.AreEqual(1, captured.ProcessedPatches);
            Assert.AreEqual(2, captured.TotalPatches);
            Assert.AreEqual(50.0, captured.Percent);
            Assert.AreEqual("process_patches", captured.Stage);
            Assert.IsNull(captured.FileName);
        }

        [TestMethod]
        public void PsrProgressRecordCarriesStructuredProgressFields()
        {
            var progress = new PsrProgress(
                Percent: 55.0,
                Stage: "native_execution",
                Message: "Generating permanent shadow raster.");

            Assert.AreEqual(55.0, progress.Percent);
            Assert.AreEqual("native_execution", progress.Stage);
            Assert.AreEqual("Generating permanent shadow raster.", progress.Message);
        }

        [TestMethod]
        public void PsrProgressCallbackAndCancellationCanBeInvoked()
        {
            PsrProgress? captured = null;
            PsrProgressCallback progressCallback = progress => captured = progress;
            PsrCancellationCallback cancelCallback = () => true;

            progressCallback(new PsrProgress(
                Percent: 100.0,
                Stage: "complete",
                Message: "Permanent shadow raster generation complete."));

            Assert.IsNotNull(captured);
            Assert.AreEqual(100.0, captured.Percent);
            Assert.AreEqual("complete", captured.Stage);
            Assert.IsTrue(cancelCallback());
        }
    }
}
